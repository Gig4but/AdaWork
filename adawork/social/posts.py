"""Reading posts out of Facebook and Instagram pages (PLAN §4.3), from the page HTML.

Kept apart from the browser, so it works on saved pages. Meta changes its markup often; the
parsing leans on the parts that have been stable for years (feed items with `aria-posinset`
or `role="article"`, message blocks marked `data-ad-preview="message"`, permalinks with
`/posts/` or `/permalink/`, Open Graph tags on Instagram post pages) and falls back to the
longest text block of a post. When nothing is found, the reader reports that the page layout
changed instead of guessing.

**Not yet checked on live pages:** that needs a real account. The test pages are modelled on
the known structure; the first real session is the check.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.normalize.text import clean_spaces, node_text

FACEBOOK = "https://www.facebook.com"
INSTAGRAM = "https://www.instagram.com"
JOB_WORDS = re.compile(
    r"pr[aá]c|brig[aá]d|job|zam[eě]stn|voln[aá] m[ií]st|nabídk|nabidk|výpomoc|vypomoc|přivýděl",
    re.IGNORECASE,
)
_POST_ID = re.compile(r"/(?:posts|permalink)/(\d+)|story_fbid=(\d+)|/p/([\w-]+)|/reel/([\w-]+)")
_FB_PHOTO = re.compile(r"scontent|fbcdn")
# Text of pages that mean "stop now" (PLAN §4.3: stop, notify, pause the platform).
_BLOCK_TEXTS = (
    "dočasně jste zablokováni",
    "you're temporarily blocked",
    "you’re temporarily blocked",
    "try again later",
    "zkuste to znovu později",
    "please wait a few minutes before you try again",
    "your account has been locked",
    "váš účet byl uzamčen",
    "we suspect automated behavior",
    "podezření na automatizované chování",
)
_POSTS = (
    "[role='feed'], [role='article'], div[aria-posinset], article, a[href*='/marketplace/item/']"
)
_BLOCK_URLS = ("/checkpoint", "/challenge", "/suspended", "/accounts/suspended")
_LOGIN_URLS = ("/login", "/accounts/login")


class SocialBlocked(Exception):
    """The platform warned, blocked or wants a verification: stop and pause it."""


class LoginNeeded(SocialBlocked):
    """The saved session expired: the user has to log in again."""


@dataclass
class Post:
    platform: str  # facebook | instagram
    post_id: str
    url: str
    text: str
    where: str = ""  # group name, Marketplace, #hashtag
    images: list[str] = field(default_factory=list)
    posted_at: datetime | None = None


@dataclass
class Group:
    url: str
    name: str
    public: bool | None = None  # None: not shown


def check_page(url: str, html: str) -> None:
    """Raises when the page is a block, a verification or a login form."""
    path = urlsplit(url).path.lower()
    if any(path.startswith(p) for p in _LOGIN_URLS):
        raise LoginNeeded(url)
    if any(p in path for p in _BLOCK_URLS):
        raise SocialBlocked(f"verification at {url}")
    head = _notice_text(html)[:20000].lower()
    for text in _BLOCK_TEXTS:
        if text in head:
            raise SocialBlocked(text)


def _notice_text(html: str) -> str:
    """The page's own text (title, dialogs, notices) without the posts on it, which may
    quote a block message."""
    if not html:
        return ""
    tree = LexborHTMLParser(html)
    for node in reversed(tree.css(_POSTS)):
        node.decompose()
    title = tree.css_first("title")
    parts = (title.text() if title else "", tree.body.text() if tree.body else "")
    return clean_spaces(" ".join(parts))


def post_id(url: str) -> str | None:
    m = _POST_ID.search(url)
    return next((g for g in m.groups() if g), None) if m else None


def _top_level_items(tree: LexborHTMLParser) -> list[LexborNode]:
    """Feed posts without their comments (comments are articles inside a post)."""
    items = tree.css("div[aria-posinset]") or tree.css("div[role='article']")
    top = []
    for node in items:
        parent, nested = node.parent, False
        while parent is not None:
            if parent.attributes.get("role") == "article" or "aria-posinset" in parent.attributes:
                nested = True
                break
            parent = parent.parent
        if not nested:
            top.append(node)
    return top


def _message(item: LexborNode) -> str:
    node = item.css_first("[data-ad-preview='message'], [data-ad-comet-preview='message']")
    if node is not None:
        return node_text(node)
    blocks = [node_text(n) for n in item.css("div[dir='auto']")]
    return max(blocks, key=len, default="")


def facebook_feed(html: str, page_url: str, where: str = "") -> list[Post]:
    """Posts of a group feed (newest first when the group is opened chronologically)."""
    tree = LexborHTMLParser(html)
    posts = []
    for item in _top_level_items(tree):
        hrefs = (a.attributes.get("href") or "" for a in item.css("a[href]"))
        link, pid = next(((h, i) for h in hrefs if (i := post_id(h))), ("", ""))
        text = _message(item)
        if not link or len(text) < 20:
            continue
        images = [img.attributes.get("src") or "" for img in item.css("a[href*='/photo'] img")]
        posts.append(
            Post(
                "facebook",
                pid,
                urljoin(FACEBOOK, link).split("?")[0],
                text,
                where,
                [u for u in dict.fromkeys(images) if _FB_PHOTO.search(u)][:4],
            )
        )
    return posts


def facebook_groups(html: str) -> list[Group]:
    """Group links on a "your groups" or a group search page."""
    tree = LexborHTMLParser(html)
    groups: dict[str, Group] = {}
    for a in tree.css("a[href*='/groups/']"):
        m = re.match(
            r"(?:https://www\.facebook\.com)?/groups/([\w.-]+)/?$",
            (a.attributes.get("href") or "").split("?")[0],
        )
        name = clean_spaces(a.text())
        if not m or m[1] in ("joins", "feed", "discover", "create") or not name:
            continue
        url = f"{FACEBOOK}/groups/{m[1]}/"
        around = clean_spaces(a.parent.parent.text() if a.parent and a.parent.parent else "")
        public = None
        if re.search(r"veřejná skupina|public group", around, re.I):
            public = True
        elif re.search(r"soukromá skupina|private group", around, re.I):
            public = False
        if url not in groups or len(name) > len(groups[url].name):
            groups[url] = Group(url, name, public)
    return list(groups.values())


def marketplace_items(html: str) -> list[Post]:
    """Marketplace search results: title, price and place of each listing."""
    tree = LexborHTMLParser(html)
    posts: dict[str, Post] = {}
    for a in tree.css("a[href*='/marketplace/item/']"):
        m = re.search(r"/marketplace/item/(\d+)", a.attributes.get("href") or "")
        lines = [clean_spaces(t) for t in a.text(separator="\n").split("\n") if clean_spaces(t)]
        text = "\n".join(lines)
        if not m or not lines or (m[1] in posts and len(posts[m[1]].text) >= len(text)):
            continue  # the same item linked again, e.g. from its picture
        img = a.css_first("img")
        posts[m[1]] = Post(
            "facebook",
            "mp" + m[1],
            f"{FACEBOOK}/marketplace/item/{m[1]}/",
            text,
            "Marketplace",
            [src] if img is not None and (src := img.attributes.get("src")) else [],
        )
    return list(posts.values())


def instagram_grid(html: str) -> list[str]:
    """Post links of a hashtag or profile page."""
    tree = LexborHTMLParser(html)
    links = []
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if re.match(r"^(?:https://www\.instagram\.com)?/(?:[\w.]+/)?(?:p|reel)/[\w-]+/?$", href):
            url = urljoin(INSTAGRAM, href)
            if url not in links:
                links.append(url)
    return links


_IG_CAPTION = re.compile(r'^.*?\bon\s.*?:\s*["“](.*)["”]\s*\.?$|^.*?:\s*["“](.*)["”]\s*\.?$', re.S)


def instagram_post(html: str, url: str, where: str = "") -> Post | None:
    """One post page: the caption from its Open Graph description, the picture."""
    tree = LexborHTMLParser(html)
    meta = tree.css_first("meta[property='og:description']") or tree.css_first(
        "meta[name='description']"
    )
    described = (meta.attributes.get("content") or "") if meta is not None else ""
    m = _IG_CAPTION.match(described)
    caption = (m[1] or m[2]) if m else ""
    if not caption:
        h1 = tree.css_first("h1")
        caption = node_text(h1) if h1 is not None else ""
    image = tree.css_first("meta[property='og:image']")
    code = post_id(url)
    if not code or not caption.strip():
        return None
    time = tree.css_first("time[datetime]")
    posted = None
    if time is not None:
        try:
            posted = datetime.fromisoformat(
                (time.attributes.get("datetime") or "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except ValueError:
            posted = None
    return Post(
        "instagram",
        code,
        f"{INSTAGRAM}/p/{code}/",
        caption.strip(),
        where,
        [content] if image is not None and (content := image.attributes.get("content")) else [],
        posted,
    )
