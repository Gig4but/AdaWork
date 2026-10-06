# M0 spike: job sources

Date: 2026-10-06. Machine: Windows 11, plain `httpx` with a browser-like User-Agent
(Chrome 141), Czech `Accept-Language`, at least 2.5 s between requests to the same host,
and a robots.txt check before every page. No site got more than ~15 requests. The only
exceptions are 22 static codebook downloads from MPSV open data, whose robots.txt allows
everything.

- Scripts: `spikes/sources/` (`polite.py` is the shared fetch/robots/fixture helper; `s01`–`s12` are the individual probes).
- Fixtures: `tests/fixtures/sources/<source_id>/`. HTML and JSON are stored as fetched. Files are trimmed to under 1 MB, and a `.trimmed.` name marks a cut file.
- Large data (not committed): `spikes/sources/data/` (git-ignored). It holds the full `volna-mista.json` and the logs.

## 1. Summary

| Source | Access verified today | Structured data | Ads (2026-10-06) | Blockers / risks | Recommended adapter | Fixtures |
|---|---|---|---|---|---|---|
| **MPSV open data** | ✅ plain GET, ETag + Last-Modified, `304` on both conditional GETs, Range supported | Source JSON + 21 codebooks | **39,487** records | none (contains contact persons' names/e-mails: personal data) | `full_dump` JSON (ijson stream), prefer **`.json.gz` (17.5 MB vs 187 MB)** | `mpsv/` |
| **Indeed CZ** (python-jobspy 1.2.0) | ✅ 2×10 results, 0.4–0.7 s per query, no errors | jobspy DataFrame | n/a (search) | ⚠ jobspy calls `apis.indeed.com/graphql` with an embedded app key; that host's robots.txt is `Disallow: /` | Decision needed (§3): jobspy **or** robots-compliant HTML search (`cz.indeed.com/jobs`, embedded `mosaic-provider-jobcards` JSON, verified) | `indeed/` |
| **LinkedIn** (python-jobspy) | ✅ 2×10 results, 6.6–10.3 s, no 429 | jobspy DataFrame | n/a | ⚠ LinkedIn robots.txt: `User-agent: * Disallow: /` and `/jobs-guest/` explicitly disallowed | Decision needed (§3); if kept: jobspy, ≤100/run, off by default | `linkedin/` |
| **prace.cz** | ✅ listing + detail, no anti-bot | JSON-LD `JobPosting` on detail | **19,567** (Praha 4,475) | `/search/`, `/hledat/` disallowed (not needed); ~half of the listing cards link out to `*.jobs.cz/pd/` company pages | generic JSON-LD; discovery from `/nabidky/<region>/?page=N` | `prace_cz/` |
| **jenprace.cz** | ✅ (Cloudflare in front, no challenge) | JSON-LD `JobPosting` incl. `baseSalary` | **24,071** | `/nabidky` without a town **redirects by Geo-IP** (here: Plzeň) → always use explicit town slugs | generic JSON-LD | `jenprace/` |
| **profesia.cz** | ✅ | **Microdata** (not JSON-LD) on standard template; **none** on branded templates | **1,407** | branded employer templates have no structured data | generic microdata/JSON-LD + HTML fallback (`og`/meta, AI) | `profesia/` |
| **startupjobs.cz** | ✅ (Cloudflare, no challenge) | JSON-LD `JobPosting` on detail; listing is client-rendered Nuxt | **396** (offers sitemap) | listing has no ads in HTML | sitemap `sitemap/offers.xml` → detail JSON-LD | `startupjobs/` |
| **jobs.cz** | ✅ **plain HTTP works for listing and detail** (not JS-only) | none (no JSON-LD); `dataLayer` product JSON + stable `data-test="jd-*"` HTML | **16,542** (Praha 7,903) | `/api/`, `/iapi/` disallowed; detail may redirect to `/fp/<company>/<id>/` branded pages | custom HTML adapter, **no browser** | `jobs_cz/` |
| **volnamista.cz** | ⛔ **not fetched**: robots.txt allows only named search engines; `User-agent: * Disallow: /` | (`__NEXT_DATA__` per plan, unverified) | ~35–40k (plan) | robots.txt blocks generic crawlers | **drop** (or ask Seznam for a feed) | `volnamista/robots.txt` |
| **prace.bazos.cz** | ✅ | HTML only | **5,958** | search/locality params disallowed → crawl the 24 categories; region filtering done locally (PSČ on every ad) | custom HTML (category paging `/<cat>/<offset>/`) + AI | `bazos/` |
| **hitprace.cz** | ✅ (Cloudflare, no challenge) | JSON-LD `JobPosting` on detail | Brno 15 km: 4,306 | aggregator (IDs `ru…`, `GIG…`, hashes) → heavy duplicates; `/firma/` disallowed | generic JSON-LD; discovery from `/prace/<town>/` (`data-detail-url`) or sitemap | `hitprace/` |
| **dobraprace.cz** | ✅ | JSON-LD `JobPosting` | **7,665** (ads sitemap) | sitemap has no `lastmod`; same operator as jobsik.cz (INET-SERVIS.CZ) | sitemap `sitemap/ads/` → generic JSON-LD | `dobraprace/` |
| **jobsik.cz** | ✅ | HTML only | 3,874 | sitemap lists only search pages; probably same inventory as dobraprace | skip at first (dedup against dobraprace), else HTML | `jobsik/` |
| **fajn-brigady.cz** | ✅ | HTML only (label: value block) | **2,112** active (detail sitemap with `lastmod`) | many `Disallow` filter params (not needed) | sitemap `inzeraty_detail.xml` → rule-based HTML parser | `fajn_brigady/` |
| **Jooble API** | doc page behind Cloudflare challenge; API answers 403 without key | JSON API | n/a | needs a key (form on jooble.org/api/about) | API (M7, user-supplied key) | `jooble/robots.txt` |
| **Careerjet API** | ✅ docs read; v4 answers 401 without key | JSON API | n/a | needs publisher account + API key; `user_ip` & `user_agent` required | API v4 (M7, user-supplied key) | `careerjet/robots.txt` |
| **Forums/boards** | ✅ 11 sections load without login | HTML only | small (tens–hundreds) | MLM/scam-heavy | generic forum source + AI | `forums/`, `resources/forums.yaml` |

## 2. Deviations from PLAN.md

1. **jobs.cz is not JS-rendered.** Plain GET of `/prace/<region>/?q[]=…` returns 30 server-rendered
   `SearchResultCard`s with title, salary, company, place, date. Detail pages are server-rendered too.
   → Custom HTML adapter **without a browser** (`needs_browser=False`). There is no JSON-LD.
2. **volnamista.cz cannot be scraped while respecting robots.txt.** `User-agent: *` gets `Disallow: /`.
   Only Googlebot, SeznamBot, Bingbot and similar are allowed. → Remove it from the top 10, or treat it
   as "needs permission/feed". Nothing from it was fetched apart from robots.txt.
3. **LinkedIn and Indeed through python-jobspy conflict with robots.txt** (see §3). The plan treats them
   as unproblematic library sources.
4. **profesia.cz uses microdata, not JSON-LD**, and branded templates have no structured data.
5. **startupjobs.cz listing pages carry no ads** (Nuxt client-side). The plan said "JSON-LD on listing
   pages". The offers sitemap plus detail JSON-LD works instead.
6. **MPSV ships a gzip variant** `volna-mista.json.gz` (17.5 MB). Use it instead of the 187 MB JSON.
   It has its own ETag and Last-Modified (generated ~3 h after the JSON).
7. **jenprace.cz geo-redirects** `/nabidky` by IP. Discovery must use explicit town or region slugs.
8. **prace.cz and jobs.cz share inventory** (both Alma Career). prace.cz listings link to
   `<company>.jobs.cz/pd/<id>` pages. Indeed CZ and hitprace re-list ads from jenprace, nastalo.info,
   superkariera and others. Dedup (PLAN §4.4 step 7) is essential, and the `job_url_direct` from Indeed
   is a good dedup key.
9. **jobsik.cz and dobraprace.cz have the same operator** (INET-SERVIS.CZ, IČ 27523209). Treat them as
   one source.
10. **No portal gives GPS**, apart from startupjobs (lat/lon in the Nuxt payload). **IČO comes only from
    MPSV**, and occasionally from the ad text (jenprace agency footer). MPSV gives `kodAdresnihoMista`
    (RÚIAN address point code) for 72 % of workplaces. A RÚIAN lookup can turn that into exact
    coordinates, which is better than the free-text geocoding in PLAN §4.4 step 6.

## 3. Legal/robots findings that need a decision (PLAN §12, §14)

- **LinkedIn**: robots.txt is `User-agent: * / Disallow: /`, plus explicit `Disallow: /jobs-guest/` for
  named bots. It says "email whitelist-crawl@linkedin.com to apply". python-jobspy uses
  `/jobs-guest/jobs/api/seeMoreJobPostings/search` and `/jobs/view/<id>`. It works today (no 429 at 20
  results), but it breaks robots.txt. Recommendation: keep it **off by default** with a warning, like
  Facebook/Instagram, or drop it.
- **Indeed**: jobspy uses `https://apis.indeed.com/graphql` with a mobile-app API key embedded in the
  library (`indeed-api-key` in `jobspy/indeed/constant.py`). `apis.indeed.com/robots.txt` =
  `Disallow: /`. A robots-compliant alternative was verified: `https://cz.indeed.com/jobs?q=…&l=…` is
  allowed by `cz.indeed.com/robots.txt` (`Allow: /`, pages `start=0..90`). Plain GET returned 200 with
  15 job cards as JSON in `window.mosaic.providerData["mosaic-provider-jobcards"]`, with fields
  `jobkey, title, company, formattedLocation, extractedSalary{min,max,type}, pubDate,
  thirdPartyApplyUrl, sponsored, snippet…`. Cloudflare is in front, so expect challenges at volume.
  Recommendation: a custom adapter on the HTML search. Fall back to jobspy only if the user opts in.
- **hitprace.cz** disallows `ClaudeBot`/`GPTBot` (training crawlers). The `*` group, which the desktop
  app falls under, allows search pages, and the `Content-signal` is `search=yes, ai-input=yes, ai-train=no`.
  Local AI extraction for the user's own search counts as ai-input → OK.
- **Personal data in fixtures**: the MPSV records (contact person name, e-mail, phone), the bazos ads
  (first name, phone prefix) and jobspy `emails` hold real contact data, all from public ads/open data.
  Fixtures are kept as fetched, as the spike brief asked. Consider pseudonymising them before the repo
  goes public.

## 4. Per-source notes

### MPSV open data (`mpsv`)
- `HEAD https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json` → 200, `Content-Length 186805339`,
  `Last-Modified: Mon, 05 Oct 2026 20:06:40 GMT`, `ETag "b226c5b-65d1d6b8b7400"`, `Accept-Ranges: bytes`.
  `If-None-Match` → **304**, `If-Modified-Since` → **304**. Full download took 27.9 s, and the ijson stream
  of all records took 47.9 s.
- Directory listing `…/volna-mista/` also offers `.json.gz` (17,513,767 B), `.jsonld(.gz)`, `.schema.json`,
  and `-metadata.jsonld`.
- Top level: `{"polozky": [...]}`, **39,487 records**.
- Fill rates (full list in `mpsv/schema.json`; JSON Schema in `mpsv/volna-mista.schema.json`):
  - 100 %: `id`/`portalId`/`referencniCislo`, `pozadovanaProfese.cs` (title), `profeseCzIsco.id`,
    `mesicniMzdaOd`, `typMzdy`, `smennost`, `pracovnePravniVztahy[]`, `minPozadovaneVzdelani`,
    `datumVlozeni`, `datumZmeny`, `terminZahajeniPracovnihoPomeru`, `zamestnavatel.nazev` + `.ico`.
  - `mesicniMzdaDo` 41 %, `pocetHodinTydne` 56 %, `upresnujiciInformace.cs` (description) 88 %.
  - Workplace (`mistoVykonuPrace`): `pracoviste[].adresa` 86 %, of which `kodAdresnihoMista` (RÚIAN)
    72 %, `obec.id` 86 %, `kraj.id` 84 %. The other variants are `okresy[]` and `obec` only, with
    `typMistaVykonuPrace` naming the variant.
  - `expirace` is only 6 %, so expiry has to be inferred from the record disappearing in the next daily dump.
  - Contact (`prvniKontaktSeZamestnavatelem`): e-mail 89 %, phone 74 %.
  - Flags: `cizinecMimoEu`, `modraKarta`, `zamestnaneckaKarta`, `azylant`, `statniSpravaSamosprava`,
    `souhlasAgentury*`; `vhodnostiPracovnihoMista[]`, `vyhodyVolnehoMista[]` (benefits 46 %),
    `pozadovanaDovednost[]` 20 %, `pozadovanaJazykovaZnalost[]` 7 %.
  - No GPS.
- Codebooks (`https://data.mpsv.cz/od/soubory/ciselniky/<name>.json`, `{"polozky":[{id,kod,nazev{cs},…}]}`).
  All 21 referenced prefixes resolve. Map is in `spikes/sources/s11_mpsv_codebooks.py`; saved under
  `mpsv/codebooks/` (`casti-obci` as a 50-item sample because it is 2.2 MB):
  `Okres→okresy (78)`, `Obec→obce (6,258)`, `Kraj→kraje (14, kodNuts3)`, `CastObce→casti-obci (15,106)`,
  `CzIsco→cz-isco (1,995, incl. oboryCinnosti)`, `PracovnepravniVztah→pracovnepravni-vztahy (5)`,
  `Smennost→smennosti (10)`, `TypMzdy→typy-mzdy (2)`, `VzdelaniDetailniKategorie (16)`,
  `VzdelaniKkov (1,630)`, `VhodnostProTypZamestnance (7)`, `VyhodyVolnehoMista (10)`,
  `Dovednost→dovednosti (103)`, `Jazyk→jazyky (22)`, `UrovenZnalostiJazyka (3)`,
  `KontaktniPracoviste (269)`, `TypMistaVykonuPrace (6)`, `TypStavebnihoObjektu (4)`,
  `MestskyObvodMestskaCast (142)`, `MestskyObvodVPraze (10)`, `ZverejnovatVpm (4)`.
- Adapter: `full_dump=True`. Daily conditional GET of the `.gz` → stream with ijson → upsert by `portalId`
  → expire records that are missing.

### python-jobspy: Indeed (`indeed`) and LinkedIn (`linkedin`)
- `scrape_jobs(site_name=[…], search_term, location, results_wanted=10, country_indeed="Czech Republic", hours_old=336)`.
  `linkedin_fetch_description` is deprecated in 1.2.0; use `fetch_description`.
- Indeed "skladník"/Praha and "účetní"/Brno: 10/10 rows each.
  - Always filled: `id, title, company, location ("Praha, A, CZ"), date_posted, job_url, job_url_direct,
    description (markdown), is_remote, company_url`.
  - Partly filled: `job_type` 6/10, salary 1/10 and 5/10 (`interval=monthly`, CZK), `emails` 4/10.
  - `job_url_direct` often points to jenprace.cz, nastalo.info, superkariera.cz or an employer career
    page, which makes it a good cross-source dedup key.
- LinkedIn "skladník"/Praha with descriptions: 10 rows in 10.3 s. Filled: `title, company, location,
  date_posted, job_type, job_level, company_industry, description, company_logo`. No salary and no direct URL.
  Without descriptions: 10 rows in 6.6 s, with title, company, location and date only.
- No rate-limit responses at this volume. jobspy's LinkedIn scraper sleeps 3–7 s between pages.
- Fixtures: `indeed/html_search_jobcards.json` (15 cards from the HTML search) and
  `indeed/html_search.trimmed.html`. The jobspy results (real ads with contact data) are not kept.

### prace.cz (`prace_cz`)
- robots.txt: disallows `/search/`, `/hledat/`, `/auth/`, `/muj/`, `/odpovedni-formular/`. Sitemap index on CloudFront.
- Listing: `/nabidky/` (19,567), `/nabidky/<region>/` (e.g. `praha`, 4,475), `?page=N`, about 30 cards
  per page. Cards link to `/nabidka/<uuid>/?rps=…` (prace.cz detail, ~24 per page) or to
  `https://<company>.jobs.cz/pd/<id>` (branded, ~14 per page). Keyword search lives under the disallowed
  `/hledat/`, so filter locally.
- Detail JSON-LD fields: `title, description (HTML), identifier{name,value}, datePosted, validThrough,
  employmentType[], hiringOrganization.name, jobLocation.address{streetAddress, addressLocality,
  postalCode, addressCountry}`.
  - No `baseSalary` in either sample (both "Plat neuveden"). Salary JSON-LD for ads that state a salary
    still needs checking in M3.
  - **Quirk:** `validThrough` is malformed (`"2026-11-01T23:59:59+01:00T23:59"`), so the parser must be lenient.
- Adapter: generic JSON-LD. Branded `*.jobs.cz/pd/` links go through the jobs.cz adapter.

### jenprace.cz (`jenprace`)
- robots.txt: `/nabidky*page_dbox*`, `*do=infinityNextPage*`, `/data/*`, `/mosaic/*`, `/ucet/prihlaseni/*`
  are disallowed. Sitemap: `/sitemap.xml`. The file also points AI agents to `/llms.txt`.
- Listing: `/nabidky/<town-or-profession-slug>?page=N`, 100 detail links per page (`/nabidka/<code>/<slug>`).
  Total 24,071. `/nabidky` without a slug geo-redirects (saved as `listing_geoip_redirect_plzen.html`).
- Detail JSON-LD: `title, description, datePosted, validThrough, url, employmentType, hiringOrganization.name,
  baseSalary{currency, value{minValue,maxValue,unitText}}, jobLocation.address{streetAddress?, addressLocality,
  postalCode?, addressRegion?, addressCountry}, jobBenefits`. Agency ads show the agency's IČO in the HTML.
- Adapter: generic JSON-LD.

### profesia.cz (`profesia`)
- robots.txt: `/cz/` (sic), `*search_offers*form=`, `*company_details.php`, `*ajax`, `*print=1` and similar
  are disallowed. Sitemap: `sitemap.php`.
- Listing: `/prace/` (1,407 ads), regions `/prace/<kraj-slug>/` (14 + `zahranici`), `?page_num=N`.
  Detail URLs: `/prace/<company-slug>/O<id>`.
- Detail: the standard template has **microdata** `JobPosting` (`title, hiringOrganization (string),
  jobLocation.address (string), employmentType (Czech text), description, datePosted`), with salary only
  in HTML (`salary-range`: "Od 3 000 EUR/měsíc"). The branded ManpowerGroup template has **no** microdata
  or JSON-LD; only `<meta>` "Místo práce: …" and HTML.
- Adapter: generic structured-data parser (microdata path) + HTML fallback + AI.

### startupjobs.cz (`startupjobs`)
- robots.txt: everything allowed except `/admin/`. Sitemap index → `sitemap/offers.xml`, 396 offers, no lastmod.
- `/nabidky` is a Nuxt SPA. The SSR payload holds only filter codebooks, no offers, so it is unusable for discovery.
- Detail `/nabidka/<id>/<slug>` JSON-LD: `title, description, datePosted, employmentType, hiringOrganization
  {name, logo, url, sameAs, description}, identifier, jobBenefits, qualifications, skills, directApply,
  baseSalary (when stated), jobLocation.address (locality/region/country)` or `jobLocationType=TELECOMMUTE`
  + `applicantLocationRequirements`. The Nuxt payload also has `lat`/`lon`.
- Adapter: sitemap discovery + generic JSON-LD. The source is small and IT/startup-heavy.

### jobs.cz (`jobs_cz`)
- robots.txt: `/api/`, `/iapi/`, `/muj/`, `/js/`, `/nabidky-podle-cv/` and similar are disallowed. No sitemap is
  listed. No Cloudflare.
- **Listing works over plain HTTP:**
  - `/prace/<region>/?q[]=<keyword>&page=N` gives 30 `article.SearchResultCard` per page (text: title,
    salary "31 000 – 45 000 Kč", company, place, date or "Příležitost dne"), with `data-jobad-id` and
    links to `/rpd/<id>/`.
  - Counts: Praha 7,903; CZ 16,542 (`/prace/?page=2`). `/prace/` without parameters is a landing page.
- Detail: `/rpd/<id>/` either stays (standard) or redirects to `/fp/<company>-<cid>/<id>/` (company profile
  layout). There is no JSON-LD. Both layouts use stable `data-test` hooks:
  - `jd-info-location` (street address)
  - `jd-salary`
  - `jd-body-richtext` (description)
  - `jd-benefits`
  - `jd-info-item` (company, education, languages, category, contract type)
- `dataLayer.push({"ecommerce":{"detail":{"products":[{name, id, brand:"<company> <companyId>",
  category:"JD/<obor>/<profese>"}]}}})` gives id, title, company and category as JSON.
- Branded `<company>.jobs.cz/pd/<id>` career pages (linked from prace.cz) were not sampled. Expect
  free-form HTML → AI.
- Adapter: custom HTML, `needs_browser=False`.

### volnamista.cz (`volnamista`)
- Only robots.txt was fetched (`volnamista/robots.txt`). Generic agents get `Disallow: /`, and
  multi-choice filter URLs (`|`, `,`) and `/click` are disallowed even for search engines. Not scraped.

### prace.bazos.cz (`bazos`)
- robots.txt disallows `hledat=`, `hlokalita=`, `humkreis`, `order=`, `cenaod/cenado`, `rubriky=` and similar.
  Sitemap: `sitemap.php`.
- 24 categories (`/administrativa/`, `/logistika/`, …, `/brigada/`, `/ostatni/`). Paging is
  `/<category>/<offset>/` (20 per page). There were 5,958 ads in total.
- Listing rows: title, date `[6.10. 2026]`, a snippet, and the locality/PSČ in the row.
- Detail `/inzerat/<id>/<slug>.php` is HTML only: `h1`, a table (Jméno, Telefon (masked), Lokalita
  "274 01 Kladno", Vidělo, Mzda/plat "Dohodou"/"V textu"), and `div.popisdetail` (free text).
- Adapter: custom HTML + AI extraction. Filter by PSČ locally.

### hitprace.cz (`hitprace`)
- robots.txt (`*`): `/admin/`, `/firma/`, `/profil/`, `/apply/`, `/webservices/` are disallowed. The
  content signal is `search=yes, ai-input=yes, ai-train=no`. The sitemap index has `sitemap-prace1.xml…`,
  lokality, profese.
- Listing: `/prace/<town>/` ("4 306 nabídek práce v Brně", 15 km radius). Only 10 cards per page, and
  their detail URLs sit in `data-detail-url="/prace-detail/<slug>-<id>/"`, not in `href`. Paging:
  `/prace/?lokalita_textbox=Brno&obor_textbox=&form_geolocation_okruh=15&strana=N`.
- Detail JSON-LD: `title, description, datePosted, validThrough, url, identifier{HitPrace.cz, id},
  hiringOrganization.name, employmentType, jobLocation.address{locality, postalCode?, region?},
  baseSalary (when stated), jobImmediateStart`.
- It is an aggregator (ID prefixes `ru`, `GIG`, `GR`, hashes), so expect many duplicates.
- Adapter: generic JSON-LD. Discovery is either sitemap-based or from town listings.

### dobraprace.cz (`dobraprace`) and jobsik.cz (`jobsik`)
- dobraprace robots.txt: `*` allows everything. The sitemap index has 9 sub-sitemaps; `sitemap/ads/`
  lists **7,665** detail URLs (`/<id>-<slug>.html`) without lastmod.
- Detail JSON-LD: `title, description, datePosted, validThrough, employmentType (sometimes),
  hiringOrganization.name, jobLocation.address{locality, region, country}, applicantLocationRequirements,
  industry`. No salary in JSON-LD, though the HTML has many "Kč" mentions.
- jobsik.cz: same operator. Its sitemap has only search pages (3,400 URLs). Listing:
  `/volna-pracovni-mista/region-<x>/`. Detail: `/nabidka-<id>/`, HTML only. The Praha region shows
  3,874 offers.
- Adapter: dobraprace via sitemap + JSON-LD. Diff the ID set daily, because there is no lastmod. Skip
  jobsik initially.

### fajn-brigady.cz (`fajn_brigady`)
- robots.txt: a long list of filter-parameter disallows plus 16 sitemaps.
  `static/sitemap/sitemap_fb/inzeraty_detail.xml` (498 KB) has **2,112** active ads with `lastmod`, which
  supports incremental updates. URLs: `/brigady/<category>/<town>/<id>-<slug>/`.
- Detail: no JSON-LD or microdata. Plain text block "pracoviště …  Termín: Ihned  Mzda: 20 000 - 23 000 Kč/
  měs.  Počet míst: 3  Vhodné i pro: Důchodce, OZP  Firma: …  Zadavatel: …  Lokalita: …  ID inzerátu: …
  Vloženo: 09.09.2026". Good for regex rules, and the meta description is the first lines of the ad.
- Adapter: sitemap + rule-based HTML parser.

### Jooble (`jooble`)
- API (key per publisher): `POST https://jooble.org/api/<key>`. The body shape below comes from Jooble's
  published docs as previously known; it was not re-read today. Request: JSON body `{"keywords",
  "location", "radius", "salary", "page", "ResultOnPage"}`. Response: `{totalCount, jobs:[{title,
  location, snippet, salary, source, type, link, company, updated, id}]}`. Today's only check: a POST
  with a fake key returned **403**.
- Signup and terms page: `https://jooble.org/api/about`. It shows a Cloudflare JS challenge to plain HTTP,
  so it was not read today. The plan's "500 requests per key in total" could not be re-verified.
  robots.txt for cz.jooble.org disallows `/desc/`, `/away/`, `/SearchResult` and similar, so the HTML is
  not an alternative.

### Careerjet (`careerjet`)
- Docs: `https://www.careerjet.com/partners/api/`, read today. Endpoint
  `GET https://search.api.careerjet.net/v4/query`. Auth is HTTP Basic: the username is the API key and
  the password is empty. Without a key the endpoint returns **401** "You did not provide an API key".
- Required params: `user_ip`, `user_agent` (the end user's). Other params: `locale_code=cs_CZ`,
  `keywords`, `location`, `contract_type (p/c/t/i/v)`, `work_hours (f/p)`, `sort (relevance|date|salary)`,
  `page 1–10`, `page_size ≤100`, `radius`.
- Response: `{type:"JOBS", hits, pages, jobs:[{title, company, date, description(excerpt), locations,
  salary, salary_min, salary_max, salary_currency_code, salary_type Y/M/W/D/H, url}]}`. A
  `type:"LOCATIONS"` answer means the location was ambiguous.
- The key comes from a publisher account. The legacy `public.api.careerjet.net` now demands a declared
  Referer and affiliate ID. The careerjet.cz robots.txt disallows `/job/`, `/jobview/`, so use the API only.

### Forums / public boards (`forums`)
- 11 sections verified (HTTP 200, no login wall, robots allows, no Cloudflare challenge). They are listed
  in `resources/forums.yaml`, with fixtures in `forums/*.html` and `_verification.json`.
  - Classifieds: HyperInzerce (práce, brigády), Avízo (volná místa, brigády), Annonce (nabídky, brigády),
    Bazar.cz, cBazar, Mimibazar.
  - Discussions: eMimino "Zaměstnání", Okoun "Práce".
- No board has JobPosting structured data. All need the generic HTML block detector + keyword filter + AI.
  Classifieds are MLM/"tipař"-heavy, so the red_flags check matters.
- Rejected: sbazar.cz (robots disallow), modrykonik (no job section), several 404/500/403 candidates
  (listed at the end of forums.yaml).

## 5. Recommended adapter map (input to M1/M3/M6)

| Adapter type | Sources |
|---|---|
| `full_dump` JSON | MPSV |
| Generic structured data (JSON-LD + microdata) | prace.cz, jenprace.cz, hitprace.cz, dobraprace.cz, startupjobs.cz (detail), profesia.cz (+HTML fallback) |
| Sitemap discovery | startupjobs, dobraprace, fajn-brigady (lastmod), hitprace (optional) |
| Custom HTML (no browser) | jobs.cz, prace.bazos.cz, fajn-brigady.cz, Indeed CZ HTML search (if chosen) |
| Library (jobspy) | LinkedIn, Indeed (only if the robots/terms decision in §3 allows) |
| API with user key | Jooble, Careerjet |
| Generic forum | `resources/forums.yaml` |
| Dropped / on hold | volnamista.cz (robots), jobsik.cz (duplicate of dobraprace) |

No source needed a headless browser today. Cloudflare sits in front of jenprace, startupjobs, hitprace
and Indeed, but it served no challenge at spike volumes. The HTTP layer should still detect challenge
pages (`cf-chl`, "Just a moment") and pause the source (PLAN §4.2).
