"""UI texts. Czech is the default; English is selectable in the wizard and Settings."""

TEXTS: dict[str, dict[str, str]] = {
    "nav.search": {"cs": "Hledat", "en": "Search"},
    "wizard.title": {"cs": "Průvodce nastavením", "en": "Setup wizard"},
    "wizard.step": {"cs": "Krok {n} ze {total}", "en": "Step {n} of {total}"},
    "wizard.next": {"cs": "Další", "en": "Next"},
    "wizard.back": {"cs": "Zpět", "en": "Back"},
    "wizard.welcome": {
        "cs": "Všechny nabídky práce ve vašem okolí na jednom místě",
        "en": "Every job near you, in one place",
    },
    "wizard.welcome.text": {
        "cs": "Aplikace hledá volná místa na úřadu práce, pracovních portálech a dalších "
        "zdrojích a ukáže vám ty, které vám dávají smysl. Nastavení zabere minutu.",
        "en": "The app collects open jobs from the labour office, job portals and other "
        "sources and shows you the ones that make sense for you. Setup takes a minute.",
    },
    "wizard.language": {"cs": "Jazyk aplikace", "en": "App language"},
    "wizard.theme": {"cs": "Barevné schéma", "en": "Colour scheme"},
    "theme.system": {"cs": "Podle Windows", "en": "Follow Windows"},
    "theme.light": {"cs": "Světlé", "en": "Light"},
    "theme.dark": {"cs": "Tmavé", "en": "Dark"},
    "theme.ocean": {"cs": "Oceán", "en": "Ocean"},
    "theme.forest": {"cs": "Les", "en": "Forest"},
    "theme.sunset": {"cs": "Západ slunce", "en": "Sunset"},
    "theme.plum": {"cs": "Švestka", "en": "Plum"},
    "theme.midnight": {"cs": "Půlnoc", "en": "Midnight"},
    "wizard.profile": {
        "cs": "Odkud dojíždíte a co hledáte",
        "en": "Where you commute from and what you look for",
    },
    "wizard.home": {"cs": "Odkud budete dojíždět", "en": "Where you will commute from"},
    "wizard.home.hint": {
        "cs": "Přesná adresa nebo jen obec, např. „Kolín“. Slouží k výpočtu doby cesty.",
        "en": "A precise address or just a town, e.g. “Kolín”. Used to compute travel times.",
    },
    "wizard.mode": {"cs": "Jak obvykle cestujete", "en": "How you usually travel"},
    "mode.any": {"cs": "Nevím, ukaž vše", "en": "Not sure, show all"},
    "mode.car": {"cs": "Auto", "en": "Car"},
    "mode.transit": {"cs": "MHD a vlaky", "en": "Public transport"},
    "mode.walk": {"cs": "Pěšky", "en": "Walking"},
    "mode.bike": {"cs": "Kolo", "en": "Bike"},
    "wizard.max_minutes": {"cs": "Nejdelší cesta do práce", "en": "Longest commute"},
    "wizard.later": {
        "cs": "Vše můžete později změnit v Nastavení.",
        "en": "You can change all of this later in Settings.",
    },
    "wizard.kinds": {"cs": "Jakou práci hledáte (nepovinné)", "en": "Kinds of work (optional)"},
    "wizard.kinds.hint": {
        "cs": "Vlastními slovy, oddělte čárkou, např. „skladník, učitelka ve školce“. "
        "Nabídky tohoto druhu stáhneme jako první a předvyplníme do hledání. "
        "Prázdné = všechny obory.",
        "en": "In your own words, separated by commas, e.g. “warehouse, nursery teacher”. "
        "Jobs of these kinds are fetched first and filled into the search. Empty = every field.",
    },
    "wizard.done": {
        "cs": "Vše je nastaveno, můžeme stáhnout první nabídky",
        "en": "All set, ready to fetch your first jobs",
    },
    "wizard.done.text": {
        "cs": "Po kliknutí na Start stáhneme nabídky z úřadu práce (všechny obory, asi minuta) "
        "a potom z vybraných portálů pro kraje ve vašem dosahu. Portály čteme pomalu, aby "
        "nás nezablokovaly, takže to může trvat i desítky minut. Nabídky přibývají průběžně.",
        "en": "Start downloads job offers from the labour office (every field, about a "
        "minute), then from the chosen portals for the regions within your reach. Portals "
        "are read slowly so they do not block us, which can take tens of minutes. Jobs "
        "appear as they come in.",
    },
    "wizard.sources": {"cs": "Kde máme nabídky hledat", "en": "Where to look for jobs"},
    "sources.title": {"cs": "Zdroje nabídek", "en": "Job sources"},
    "sources.text": {
        "cs": "Vyberte, odkud má aplikace nabídky stahovat.",
        "en": "Choose where the app downloads job offers from.",
    },
    "sources.kind.portal": {"cs": "Úřad práce a portály", "en": "Labour office and portals"},
    "sources.kind.classifieds": {"cs": "Inzertní servery", "en": "Classified ad boards"},
    "sources.classifieds.hint": {
        "cs": "Inzeráty od kohokoli: nabídky, které nejsou prací (půjčky, služby, lidé hledající "
        "práci), aplikace nezobrazí, a podezřelé nabídky (poplatky, MLM, „práce doma“) skryje, "
        "dokud je ve filtrech nezapnete.",
        "en": "Ads written by anyone: posts that are not jobs (loans, services, people looking "
        "for work) are not shown, and suspicious offers (fees, MLM, 'work from home') stay "
        "hidden until you switch them on in the filters.",
    },
    "sources.off_default": {"cs": "ve výchozím stavu vypnuto", "en": "off by default"},
    "sources.linkedin.warning": {
        "cs": "LinkedIn automatické čtení svých stránek zakazuje, a to v podmínkách použití "
        "(User Agreement) i v souboru robots.txt. Aplikace čte jen veřejné nabídky bez "
        "přihlášení, váš účet tedy v ohrožení není. LinkedIn ale může váš počítač (IP adresu) "
        "na čas zablokovat. Aplikace proto čte pomalu, nejvýš 100 nabídek za jedno hledání, "
        "a při prvním odmítnutí na 12 hodin přestane.",
        "en": "LinkedIn forbids automatic reading of its pages, both in its User Agreement and "
        "in its robots.txt rules. The app reads only public job ads without logging in, so "
        "your account is not at risk. LinkedIn may still block your computer (IP address) for "
        "a while. The app therefore reads slowly, at most 100 ads per check, and stops for 12 "
        "hours at the first refusal.",
    },
    "sources.consent": {
        "cs": "Rozumím riziku a chci tento zdroj zapnout",
        "en": "I understand the risk and want to switch this source on",
    },
    "sources.consent_missing": {
        "cs": "{source} se zapne, až zaškrtnete, že rozumíte riziku.",
        "en": "{source} is switched on only after you tick that you understand the risk.",
    },
    "sources.indeed": {
        "cs": "Indeed automatické čtení blokuje. Ve vyhledávání najdete odkaz, který stejné "
        "hledání otevře na Indeedu ve vašem prohlížeči.",
        "en": "Indeed blocks automatic reading. The search page has a link that opens the same "
        "search on Indeed in your browser.",
    },
    "sources.social.warning_title": {
        "cs": "Automatické čtení Facebooku a Instagramu je riskantní:",
        "en": "Reading Facebook and Instagram automatically is risky:",
    },
    "sources.social.terms": {
        "cs": "porušuje podmínky používání společnosti Meta;",
        "en": "it breaks Meta's terms of use;",
    },
    "sources.social.account": {
        "cs": "Facebook nebo Instagram může váš účet dočasně zablokovat, nebo ho i trvale "
        "zrušit (přijdete o Messenger, skupiny a fotky);",
        "en": "Facebook or Instagram may block your account for a while or even disable it "
        "for good (you lose Messenger, groups and photos);",
    },
    "sources.social.no_safe_limit": {
        "cs": "žádný počet příspěvků není zaručeně bezpečný;",
        "en": "no number of posts is guaranteed to be safe;",
    },
    "sources.social.how": {
        "cs": "aplikace čte pomalu (nejvýš {n} příspěvků denně, ve dvou krátkých čteních přes "
        "den), jen čte (nic nelajkuje, nekomentuje ani se nepřidává do skupin), při prvním "
        "varování přestane a vaše heslo nikdy nevidí ani neukládá.",
        "en": "the app reads slowly (at most {n} posts a day, in two short sessions in the "
        "daytime), only reads (no likes, comments or joining groups), stops at the first "
        "warning and never sees or stores your password.",
    },
    "sources.social.more": {"cs": "Více o rizicích", "en": "More about the risks"},
    "sources.instagram.separate": {
        "cs": "Tip: Instagram dovoluje mít víc účtů. Přihlaste se raději samostatným účtem, "
        "ne svým hlavním.",
        "en": "Tip: Instagram allows several accounts. Better log in with a separate account, "
        "not your main one.",
    },
    "sources.social.per_day": {"cs": "Nejvýš příspěvků denně", "en": "Posts a day at most"},
    "sources.social.per_day_hint": {
        "cs": "Výchozí je {n}. Víc znamená větší riziko pro účet.",
        "en": "The default is {n}. More means a higher risk for the account.",
    },
    "sources.facebook.groups": {
        "cs": "Další skupiny s nabídkami práce (odkazy, každý na řádek)",
        "en": "More groups with job offers (links, one per line)",
    },
    "sources.facebook.suggested": {
        "cs": "Soukromé skupiny s nabídkami poblíž. Aplikace se do skupin sama nepřidává; "
        "pokud chcete, přidejte se ve Facebooku sami:",
        "en": "Private groups with offers nearby. The app never joins groups itself; join in "
        "Facebook yourself if you like:",
    },
    "sources.instagram.tags": {
        "cs": "Další hashtagy (oddělené mezerou)",
        "en": "More hashtags (separated by spaces)",
    },
    "risk.level": {"cs": "Co se může stát", "en": "What can happen"},
    "risk.what": {"cs": "Co to znamená", "en": "What it means"},
    "risk.how_long": {"cs": "Jak dlouho", "en": "How long"},
    "risk.slow": {"cs": "Dočasné omezení „příliš rychle“", "en": "'Going too fast' block"},
    "risk.slow.what": {
        "cs": "Některá funkce (např. skupiny) je zablokovaná",
        "en": "A feature (e.g. groups) is blocked",
    },
    "risk.slow.long": {
        "cs": "Obvykle 24–48 h, Facebook ho dřív nezruší",
        "en": "Usually 24–48 h, Facebook will not lift it earlier",
    },
    "risk.checkpoint": {"cs": "Ověření totožnosti", "en": "Identity check"},
    "risk.checkpoint.what": {
        "cs": "Před dalším použitím musíte potvrdit totožnost (kód, někdy fotka nebo doklad)",
        "en": "You must confirm your identity (a code, sometimes a photo or an ID) first",
    },
    "risk.checkpoint.long": {"cs": "Dokud ho nevyřešíte", "en": "Until you resolve it"},
    "risk.action": {"cs": "Blokace akcí na Instagramu", "en": "Instagram action block"},
    "risk.action.what": {"cs": "Akce jsou zablokované", "en": "Actions are blocked"},
    "risk.action.long": {
        "cs": "První obvykle 24–72 h, další déle",
        "en": "The first usually 24–72 h, later ones longer",
    },
    "risk.restricted": {"cs": "Dočasné omezení účtu", "en": "Temporary restriction"},
    "risk.restricted.what": {
        "cs": "Omezené funkce (skupiny, Marketplace)",
        "en": "Limited features (groups, Marketplace)",
    },
    "risk.restricted.long": {"cs": "Dny až týdny", "en": "Days to weeks"},
    "risk.disabled": {"cs": "Trvalé zrušení účtu", "en": "Permanent disable"},
    "risk.disabled.what": {
        "cs": "Ztráta účtu včetně Messengeru, skupin, Marketplace a fotek",
        "en": "The account is lost, including Messenger, groups, Marketplace and photos",
    },
    "risk.disabled.long": {
        "cs": "Trvale (lze se odvolat)",
        "en": "Permanent (an appeal is possible)",
    },
    "risk.gdpr": {
        "cs": "Příspěvky obsahují osobní údaje. Zůstávají jen v tomto počítači a 14 dní poté, "
        "co je aplikace naposledy viděla, se smažou.",
        "en": "Posts contain personal data. They stay on this computer only and are deleted "
        "14 days after the app last saw them.",
    },
    "social.login": {"cs": "Přihlásit se", "en": "Log in"},
    "social.login_running": {
        "cs": "Otevřelo se okno Edge: přihlaste se v něm a okno pak zavřete.",
        "en": "An Edge window opened: log in there, then close the window.",
    },
    "social.logged_out": {
        "cs": "Nepřihlášeno: aplikace čte, až se přihlásíte.",
        "en": "Not logged in: the app reads once you log in.",
    },
    "social.logged_in": {
        "cs": "Přihlášeno · dnes přečteno {n} příspěvků",
        "en": "Logged in · {n} posts read today",
    },
    "social.blocked": {
        "cs": "Platforma varovala nebo blokovala: čtení je pozastavené do {until}.",
        "en": "The platform warned or blocked: reading is paused until {until}.",
    },
    "social.login_failed": {
        "cs": "Přihlášení se nedokončilo. Zkuste to znovu.",
        "en": "The login did not finish. Please try again.",
    },
    "social.login_busy": {
        "cs": "Aplikace právě čte v okně Edge. Přihlaste se, až čtení skončí.",
        "en": "The app is reading in its Edge window. Log in once the reading has finished.",
    },
    "social.stopped.blocked": {
        "cs": "zastaveno po varování platformy, pauza 7 dní",
        "en": "stopped after a warning from the platform, paused for 7 days",
    },
    "social.stopped.login": {
        "cs": "přihlášení vypršelo, přihlaste se znovu na stránce Zdroje",
        "en": "the login expired, log in again on the Sources page",
    },
    "social.stopped.layout": {
        "cs": "stránky se změnily a aplikace v nich nenašla příspěvky",
        "en": "the pages changed and the app found no posts in them",
    },
    "social.stopped.failed": {
        "cs": "stránka se nenačetla nebo bylo okno Edge zavřeno",
        "en": "a page did not load or the Edge window was closed",
    },
    "sources.save": {"cs": "Uložit", "en": "Save"},
    "sources.saved": {
        "cs": "Uloženo. Projeví se při příštím hledání.",
        "en": "Saved. It applies from the next check.",
    },
    "search.indeed": {"cs": "Hledat také na Indeed", "en": "Also search on Indeed"},
    "nav.add": {"cs": "Vložit inzerát", "en": "Add an ad"},
    "add.title": {"cs": "Vložit inzerát", "en": "Add an ad"},
    "add.heading": {
        "cs": "Přidejte nabídku, kterou jste našli jinde",
        "en": "Add a job you found elsewhere",
    },
    "add.text": {
        "cs": "Z nástěnky, novin nebo zprávy: vložte text inzerátu, nebo jeho fotku či snímek "
        "obrazovky. Umělá inteligence z něj doplní obor, místo a mzdu.",
        "en": "From a notice board, a newspaper or a message: paste the ad's text, or add a "
        "photo or screenshot of it. The AI fills in the field, place and pay.",
    },
    "add.ad_text": {"cs": "Text inzerátu", "en": "Ad text"},
    "add.placeholder": {
        "cs": "Např. Přijmeme skladníka do Kolína, mzda 32 000 Kč…",
        "en": "E.g. Warehouse worker wanted in Kolín, 32,000 CZK…",
    },
    "add.picture": {"cs": "Fotka nebo snímek obrazovky", "en": "Photo or screenshot"},
    "add.picture_hint": {
        "cs": "Text z obrázku přečte AI, když je model stažený.",
        "en": "The AI reads the text in the picture once a model is downloaded.",
    },
    "add.link": {"cs": "Odkaz (nepovinný)", "en": "Link (optional)"},
    "add.save": {"cs": "Uložit inzerát", "en": "Save the ad"},
    "add.saved": {
        "cs": "Uloženo. Chybějící údaje doplní AI na pozadí.",
        "en": "Saved. The AI fills in missing details in the background.",
    },
    "add.another": {"cs": "Vložit další", "en": "Add another"},
    "add.picture_title": {"cs": "Inzerát z obrázku", "en": "Ad from a picture"},
    "add.empty": {
        "cs": "Vložte text inzerátu nebo obrázek.",
        "en": "Paste the ad text or add a picture.",
    },
    "add.too_big": {"cs": "Obrázek je větší než 15 MB.", "en": "The picture is larger than 15 MB."},
    "add.not_picture": {
        "cs": "Soubor není obrázek (JPEG, PNG…).",
        "en": "The file is not a picture (JPEG, PNG…).",
    },
    "wizard.ai": {
        "cs": "Má umělá inteligence doplňovat údaje z textu inzerátů?",
        "en": "Should the AI fill in details from the ad texts?",
    },
    "ai.title": {"cs": "Umělá inteligence", "en": "Artificial intelligence"},
    "ai.text": {
        "cs": "Model umělé inteligence běží jen na vašem počítači, nic se nikam neposílá. "
        "U nabídek z portálů doplní obor, místo, směny, vzdělání a mzdu, když je inzerát "
        "uvádí jen v textu.",
        "en": "The AI model runs only on your computer; nothing is sent anywhere. For portal "
        "ads it fills in the field of work, place, shifts, education and salary when the "
        "ad mentions them only in its text.",
    },
    "ai.hardware": {
        "cs": "Tento počítač: {ram} GB RAM · {gpu} · {disk} GB volného místa",
        "en": "This computer: {ram} GB RAM · {gpu} · {disk} GB free disk space",
    },
    "ai.no_gpu": {"cs": "bez grafické karty", "en": "no graphics card"},
    "ai.auto": {
        "cs": "Automaticky – nejlepší pro tento počítač",
        "en": "Automatic – best for this computer",
    },
    "ai.auto.now": {"cs": "teď vybráno: {tier}", "en": "currently: {tier}"},
    "ai.auto.none": {"cs": "teď: jen pravidla", "en": "currently: rules only"},
    "ai.tier.simple": {"cs": "Jednoduchý", "en": "Simple"},
    "ai.tier.good": {"cs": "Dobrý", "en": "Good"},
    "ai.tier.excellent": {"cs": "Výborný", "en": "Excellent"},
    "ai.recommended": {"cs": "Doporučeno", "en": "Recommended"},
    "ai.fit.green": {"cs": "Poběží plynule", "en": "Runs smoothly"},
    "ai.fit.yellow": {"cs": "Poběží, ale pomaleji", "en": "Works, but slower"},
    "ai.fit.red": {"cs": "Nedoporučeno", "en": "Not recommended"},
    "ai.seconds": {"cs": "~{s} s na inzerát", "en": "~{s} s per ad"},
    "ai.measured": {"cs": "změřeno", "en": "measured"},
    "ai.downloaded": {"cs": "staženo", "en": "downloaded"},
    "ai.why.ram": {"cs": "málo paměti", "en": "not enough memory"},
    "ai.why.ram_tight": {"cs": "paměti je těsně", "en": "memory is tight"},
    "ai.why.slow": {"cs": "příliš pomalé", "en": "too slow"},
    "ai.why.slower": {"cs": "pomalejší", "en": "slower"},
    "ai.why.disk": {"cs": "málo místa na disku", "en": "not enough disk space"},
    "ai.off": {
        "cs": "Bez umělé inteligence (jen pravidla)",
        "en": "No AI (rules only)",
    },
    "ai.red_confirm": {
        "cs": "Tento model je pro váš počítač příliš náročný. Opravdu ho chcete?",
        "en": "This model is too demanding for your computer. Do you really want it?",
    },
    "ai.reason.igpu": {
        "cs": "{ram} GB RAM, integrovaná grafika → {tier}",
        "en": "{ram} GB RAM, integrated graphics → {tier}",
    },
    "ai.reason.dgpu": {
        "cs": "{ram} GB RAM, grafická karta s {vram} GB → {tier}",
        "en": "{ram} GB RAM, graphics card with {vram} GB → {tier}",
    },
    "ai.reason.cpu": {
        "cs": "{ram} GB RAM bez grafické karty → {tier}",
        "en": "{ram} GB RAM, no graphics card → {tier}",
    },
    "ai.reason.none": {
        "cs": "Model se do paměti tohoto počítače nevejde; aplikace použije jen pravidla.",
        "en": "No model fits this computer's memory; the app uses rules only.",
    },
    "ai.download.downloading": {
        "cs": "Stahuji model {model}: {percent} % ({done} z {total} GB)",
        "en": "Downloading {model}: {percent} % ({done} of {total} GB)",
    },
    "ai.download.benchmark": {
        "cs": "Měřím rychlost modelu na tomto počítači…",
        "en": "Measuring the model's speed on this computer…",
    },
    "ai.download.waiting_metered": {
        "cs": "Připojení je účtované po datech, model se proto zatím nestahuje.",
        "en": "The connection is metered, so the model is not downloaded yet.",
    },
    "ai.download.metered_ok": {"cs": "Přesto stáhnout", "en": "Download anyway"},
    "ai.download.failed": {
        "cs": "Stažení se nepovedlo: {msg}. Zkusím to znovu při příštím spuštění.",
        "en": "The download failed: {msg}. It will be retried at the next start.",
    },
    "ai.ready": {"cs": "Model {model} je připraven.", "en": "Model {model} is ready."},
    "ai.not_ready": {
        "cs": "Model zatím není stažený; aplikace používá jen pravidla.",
        "en": "No model is downloaded yet; the app uses rules only.",
    },
    "ai.notice.upgraded": {
        "cs": "Model byl vylepšen na {model}.",
        "en": "The model was upgraded to {model}.",
    },
    "ai.busy.no_model": {"cs": "model není stažený", "en": "no model downloaded"},
    "ai.busy.pc_busy": {
        "cs": "počítač je právě vytížený",
        "en": "the computer is busy right now",
    },
    "ai.busy.memory": {"cs": "málo volné paměti", "en": "not enough free memory"},
    "ai.enrich.rules": {
        "cs": "Třídím nabídky podle oboru: {done} z {total}",
        "en": "Sorting jobs by field: {done} of {total}",
    },
    "ai.enrich.ai": {
        "cs": "AI doplňuje údaje: {done} z {total} nabídek",
        "en": "AI is filling in details: {done} of {total} jobs",
    },
    "ai.enrich.waiting": {
        "cs": "AI počká ({reason}), pak bude pokračovat",
        "en": "AI is waiting ({reason}) and will continue",
    },
    "ai.enrich.done": {
        "cs": "Doplněné nabídky: {done} · obor podle názvu: {classified}",
        "en": "Jobs filled in: {done} · field from the title: {classified}",
    },
    "ai.advanced": {"cs": "Pokročilé", "en": "Advanced"},
    "ai.model_choice": {"cs": "Konkrétní model", "en": "Specific model"},
    "ai.server_url": {
        "cs": "Vlastní server (např. Ollama: http://127.0.0.1:11434)",
        "en": "Own server (e.g. Ollama: http://127.0.0.1:11434)",
    },
    "ai.server_model": {"cs": "Název modelu na serveru", "en": "Model name on the server"},
    "ai.allow_metered": {
        "cs": "Stahovat i přes připojení účtované po datech",
        "en": "Download over metered connections too",
    },
    "ai.models": {"cs": "Stažené modely", "en": "Downloaded models"},
    "ai.models.text": {
        "cs": "Modely zůstávají na disku i po změně volby, takže se k nim můžete vrátit. "
        "Smazat je můžete jen vy.",
        "en": "Models stay on disk when the choice changes, so you can go back to them. "
        "Only you can delete them.",
    },
    "ai.in_use": {"cs": "používá se", "en": "in use"},
    "ai.delete": {"cs": "Smazat", "en": "Delete"},
    "ai.delete_confirm": {
        "cs": "Smazat tento model z disku? Kdykoli ho můžete stáhnout znovu.",
        "en": "Delete this model from disk? You can download it again any time.",
    },
    "ai.save": {"cs": "Uložit", "en": "Save"},
    "ai.saved": {"cs": "Uloženo.", "en": "Saved."},
    "ask.placeholder": {
        "cs": "Zeptejte se větou, např. „sklad u Kolína, jen ranní, aspoň 30 tisíc“",
        "en": "Ask in a sentence, e.g. “warehouse near Kolín, mornings only, at least 30k”",
    },
    "ask.button": {"cs": "Zeptat se AI", "en": "Ask AI"},
    "ask.wait.downloading": {
        "cs": "Stahuji AI ({percent} %), pak se tu můžete zeptat větou…",
        "en": "Downloading the AI ({percent} %), then you can ask in a sentence here…",
    },
    "ask.wait.benchmark": {
        "cs": "Zkouším AI na tomto počítači, za chvíli se tu můžete zeptat větou…",
        "en": "Trying the AI on this computer, you can ask here in a moment…",
    },
    "ask.wait.metered": {
        "cs": "AI se nestahuje přes účtované připojení (Nastavení → AI)",
        "en": "The AI is not downloaded over a metered connection (Settings → AI)",
    },
    "ask.wait.failed": {
        "cs": "Stažení AI se nepovedlo (Nastavení → AI)",
        "en": "The AI download failed (Settings → AI)",
    },
    "ask.wait.off": {"cs": "AI je vypnutá (Nastavení → AI)", "en": "AI is off (Settings → AI)"},
    "ask.wait.none": {
        "cs": "AI zatím není připravená (Nastavení → AI)",
        "en": "The AI is not ready yet (Settings → AI)",
    },
    "ask.failed": {
        "cs": "Dotaz se nepodařilo zpracovat ({msg}). Zkuste běžné hledání.",
        "en": "The question could not be processed ({msg}). Try the normal search.",
    },
    "job.ai_badge": {"cs": "doplnila AI", "en": "added by AI"},
    "job.picture": {"cs": "Obrázek z inzerátu", "en": "Picture from the ad"},
    "job.red_flags": {"cs": "Pozor, podezřelá nabídka:", "en": "Caution, suspicious offer:"},
    "wizard.auto_off": {
        "cs": "Automatické hledání je vypnuté. Zapnout ho můžete později v Nastavení.",
        "en": "Automatic checks are off. You can switch them on later in Settings.",
    },
    "wizard.start": {"cs": "Start", "en": "Start"},
    "wizard.skip": {"cs": "Přeskočit", "en": "Skip"},
    "minutes": {"cs": "min", "en": "min"},
    "search.placeholder": {
        "cs": "Pozice, firma nebo místo – např. skladník Kolín",
        "en": "Position, company or place – e.g. skladník Kolín",
    },
    "search.found": {"cs": "Nalezené nabídky: {n}", "en": "Jobs found: {n}"},
    "search.empty": {
        "cs": "Zatím tu nejsou žádné nabídky. Klikněte na „Obnovit nabídky“.",
        "en": "No jobs yet. Click “Refresh jobs”.",
    },
    "search.more": {"cs": "Další nabídky", "en": "More jobs"},
    "search.empty_filtered": {
        "cs": "Těmto filtrům neodpovídá žádná nabídka. Zkuste některý filtr zrušit.",
        "en": "No jobs match these filters. Try removing one.",
    },
    "filters.title": {"cs": "Filtry", "en": "Filters"},
    "view.list": {"cs": "Seznam", "en": "List"},
    "view.map": {"cs": "Mapa", "en": "Map"},
    "map.empty": {
        "cs": "Nabídky zatím nemají určené místo. Doplní se po dokončení hledání nabídek.",
        "en": "The jobs have no place on the map yet. Places are filled in when a search ends.",
    },
    "map.approx": {
        "cs": "přibližná poloha (obec nebo okres)",
        "en": "approximate (town or district)",
    },
    "map.more": {
        "cs": "…a další nabídky: {n}. Upřesněte hledání nebo filtry.",
        "en": "…and {n} more. Narrow the search or the filters.",
    },
    "filters.loading": {"cs": "Načítám filtry…", "en": "Loading filters…"},
    "filters.reset": {"cs": "Zrušit filtry", "en": "Clear filters"},
    "filters.any": {"cs": "Nezáleží", "en": "Any"},
    "filters.salary": {"cs": "Mzda alespoň (měsíčně)", "en": "Salary at least (monthly)"},
    "filters.with_salary": {"cs": "Jen s uvedenou mzdou", "en": "Only with salary"},
    "filters.flagged": {
        "cs": "Zobrazit i podezřelé nabídky",
        "en": "Also show suspicious offers",
    },
    "filters.education": {"cs": "Moje vzdělání", "en": "My education"},
    "filters.education.any": {"cs": "Neuvádím", "en": "Not specified"},
    "filters.posted": {"cs": "Vloženo", "en": "Posted"},
    "filters.days": {"cs": "za posledních {n} dní", "en": "in the last {n} days"},
    "filters.day1": {"cs": "za poslední den", "en": "in the last day"},
    "filters.categories": {"cs": "Obor", "en": "Field"},
    "filters.kraj": {"cs": "Kraj", "en": "Region"},
    "filters.sources": {"cs": "Zdroje", "en": "Sources"},
    "filters.contract": {"cs": "Typ úvazku", "en": "Contract type"},
    "filters.shifts": {"cs": "Směny", "en": "Shifts"},
    "filters.suitable": {"cs": "Vhodné pro", "en": "Suitable for"},
    "filters.benefits": {"cs": "Výhody", "en": "Benefits"},
    "filters.sort": {"cs": "Řazení", "en": "Sort"},
    "sort.relevance": {"cs": "Nejrelevantnější", "en": "Most relevant"},
    "sort.date": {"cs": "Nejnovější", "en": "Newest"},
    "sort.salary": {"cs": "Nejvyšší mzda", "en": "Highest salary"},
    "job.salary_month": {"cs": "Kč/měsíc", "en": "CZK/month"},
    "job.salary_hour": {"cs": "Kč/hod.", "en": "CZK/hour"},
    "job.posted": {"cs": "Vloženo", "en": "Posted"},
    "job.open": {"cs": "Otevřít původní nabídku", "en": "Open the original ad"},
    "job.full_text": {"cs": "Celý text", "en": "Full text"},
    "job.new": {"cs": "Nová – našlo ji poslední hledání", "en": "New: found by the last search"},
    "job.no_text": {
        "cs": "Inzerát nemá popis. Celý text najdete v původní nabídce.",
        "en": "The ad has no description. The full text is in the original ad.",
    },
    "job.close": {"cs": "Zavřít", "en": "Close"},
    "job.unchecked": {"cs": "Ověřuji, zda stále platí…", "en": "Checking it is still open…"},
    "page.top": {"cs": "Nahoru", "en": "Back to top"},
    "job.hide": {"cs": "Skrýt", "en": "Hide"},
    "job.hide_hint": {"cs": "Skrýt nabídku ze seznamu", "en": "Hide the job from the list"},
    "hidden.count": {"cs": "Skryto: {n}", "en": "Hidden: {n}"},
    "hidden.restore_last": {"cs": "Vrátit poslední", "en": "Show the last one"},
    "hidden.restore_all": {"cs": "Vrátit všechny", "en": "Show all"},
    "scrape.button": {"cs": "Obnovit nabídky", "en": "Refresh jobs"},
    "scrape.button_hint": {
        "cs": "Ověří, zda nabídky v seznamu stále platí, a doplní nové nad ně.",
        "en": "Checks the jobs in the list are still open and adds the new ones above them.",
    },
    "scrape.check": {
        "cs": "Ověřuji, zda nabídky stále platí: {n}",
        "en": "Checking the jobs are still open: {n}",
    },
    "scrape.restarting": {
        "cs": "Zastavuji hledání a začínám od začátku…",
        "en": "Stopping the search to start over…",
    },
    "scrape.download": {"cs": "Stahuji data…", "en": "Downloading…"},
    "scrape.import": {"cs": "Zpracovávám nabídky: {n}", "en": "Processing jobs: {n}"},
    "scrape.images": {
        "cs": "Stahuji obrázky z inzerátů… ({n})",
        "en": "Downloading ad pictures… ({n})",
    },
    "scrape.geocode": {"cs": "Určuji polohu nabídek…", "en": "Locating jobs…"},
    "scrape.listing": {
        "cs": "Procházím {source}: {n} nabídek",
        "en": "Browsing {source}: {n} jobs",
    },
    "scrape.parallel": {"cs": "Procházím portály:", "en": "Browsing portals:"},
    "scrape.social": {
        "cs": "Čtu {source} pomalu, jako člověk…",
        "en": "Reading {source} slowly, like a person would…",
    },
    "scrape.details": {
        "cs": "Čtu podrobnosti nabídek na {source}: {n}",
        "en": "Reading job details on {source}: {n}",
    },
    "scrape.merged": {
        "cs": "Sloučeno s nabídkou z jiného portálu: {n}.",
        "en": "Merged with a job from another portal: {n}.",
    },
    "scrape.partial": {
        "cs": "Některé zdroje se nepodařilo načíst: {sources}.",
        "en": "Some sources could not be read: {sources}.",
    },
    "scrape.start": {"cs": "Spouštím…", "en": "Starting…"},
    "scrape.clear": {"cs": "Mažu dříve nalezené nabídky…", "en": "Deleting the jobs found before…"},
    "scrape.fresh": {"cs": "Obnovit od začátku", "en": "Start over"},
    "scrape.fresh_hint": {
        "cs": "Smaže všechny nalezené nabídky a najde je znovu. Ručně přidané zůstanou.",
        "en": "Deletes all found jobs and finds them again. Jobs you added yourself stay.",
    },
    "scrape.fresh_confirm": {
        "cs": "Smazat všechny nalezené nabídky, i skryté, a hledat znovu?",
        "en": "Delete all found jobs, hidden ones too, and search again?",
    },
    "scrape.done": {
        "cs": "Hotovo: {new} nových, {updated} změněných, {removed} již neplatných.",
        "en": "Done: {new} new, {updated} changed, {removed} no longer open.",
    },
    "scrape.failed": {"cs": "Stahování selhalo: {msg}", "en": "Download failed: {msg}"},
    "status.jobs": {"cs": "Aktivní nabídky: {n}", "en": "Active jobs: {n}"},
    "status.last_run": {"cs": "poslední aktualizace {when}", "en": "last updated {when}"},
    "status.auto_off": {"cs": "automatické hledání vypnuto", "en": "automatic checks off"},
    "status.next": {"cs": "další hledání {when}", "en": "next check {when}"},
    "status.paused": {
        "cs": "automatické hledání pozastaveno do {when}",
        "en": "automatic checks paused until {when}",
    },
    # Settings, wizard steps 5 and 6 (M7)
    "nav.settings": {"cs": "Nastavení", "en": "Settings"},
    "settings.title": {"cs": "Nastavení", "en": "Settings"},
    "settings.saved": {"cs": "Uloženo.", "en": "Saved."},
    "settings.data": {"cs": "Data aplikace", "en": "App data"},
    "settings.data_dir": {"cs": "Složka s daty", "en": "Data folder"},
    "settings.log_dir": {"cs": "Záznamy (logy)", "en": "Logs"},
    "settings.save": {"cs": "Uložit", "en": "Save"},
    "settings.wizard": {"cs": "Spustit průvodce znovu", "en": "Run the setup wizard again"},
    "settings.wizard.hint": {
        "cs": "Projde všechna nastavení krok za krokem.",
        "en": "Goes through all settings step by step.",
    },
    "licenses.title": {"cs": "Licence", "en": "Licenses"},
    "licenses.open": {"cs": "Zobrazit licence", "en": "Show licenses"},
    "licenses.hint": {
        "cs": "Licenční texty programů a knihoven, které AdaWork obsahuje.",
        "en": "License texts of the programs and libraries AdaWork includes.",
    },
    "licenses.intro": {
        "cs": "AdaWork obsahuje tyto programy a knihovny. Kliknutím zobrazíte licenční text.",
        "en": "AdaWork includes these programs and libraries. Click one to read its license.",
    },
    "licenses.group.notices": {"cs": "Přehled", "en": "Overview"},
    "licenses.group.programs": {"cs": "Programy", "en": "Programs"},
    "licenses.group.runtime": {"cs": "Python", "en": "Python"},
    "licenses.group.python": {"cs": "Balíčky Pythonu", "en": "Python packages"},
    "licenses.group.web": {"cs": "Webové knihovny", "en": "Web libraries"},
    "licenses.none": {"cs": "Licenční texty nebyly nalezeny.", "en": "No license texts found."},
    "settings.appearance": {"cs": "Jazyk a vzhled", "en": "Language and appearance"},
    "settings.instant": {"cs": "Změny se ukládají hned.", "en": "Changes are saved at once."},
    "settings.profile": {"cs": "Dojíždění", "en": "Commute"},
    "settings.profile.summary": {
        "cs": "{home} · {mode} · nejvýš {minutes} min",
        "en": "{home} · {mode} · up to {minutes} min",
    },
    "settings.no_home": {"cs": "domov nezadán", "en": "no home set"},
    "settings.ai.none": {"cs": "model zatím nestažen", "en": "no model downloaded yet"},
    "settings.sources.summary": {
        "cs": "zapnuto {on} z {total}",
        "en": "{on} of {total} switched on",
    },
    "settings.keys.none": {"cs": "žádný klíč", "en": "no keys"},
    "settings.off": {"cs": "vypnuto", "en": "off"},
    "auto.title": {"cs": "Automatické hledání", "en": "Automatic checks"},
    "auto.enabled": {
        "cs": "Automaticky hledat nové nabídky",
        "en": "Check for new jobs automatically",
    },
    "auto.interval": {"cs": "Jak často", "en": "How often"},
    "auto.every": {"cs": "každých {h} h", "en": "every {h} h"},
    "auto.start_with_windows": {
        "cs": "Spouštět se systémem Windows (v oznamovací oblasti)",
        "en": "Start with Windows (in the notification area)",
    },
    "auto.notifications": {
        "cs": "Upozornit mě na nové nabídky",
        "en": "Notify me about new jobs",
    },
    "auto.hint": {
        "cs": "Aplikace pak běží i se zavřeným oknem, ikonou vpravo dole u hodin. Sociální sítě "
        "se čtou nejvýš dvakrát denně. S vypnutým automatickým hledáním hledá aplikace jen po "
        "kliknutí na „Obnovit nabídky“.",
        "en": "The app then keeps running with its window closed, as an icon next to the clock. "
        "Social networks are read twice a day at most. With automatic checks off, the app "
        "searches only when you click “Refresh jobs”.",
    },
    "auto.save": {"cs": "Uložit", "en": "Save"},
    "auto.pause": {"cs": "Pozastavit na 24 h", "en": "Pause for 24 h"},
    "auto.resume": {"cs": "Pokračovat", "en": "Resume"},
    "keys.title": {
        "cs": "Klíče k dalším službám (volitelné)",
        "en": "Keys for other services (optional)",
    },
    "keys.text": {
        "cs": "Jooble, Careerjet a Google Maps fungují jen s vlastním klíčem. "
        "Klíč získáte po registraci na jejich stránkách. Uloží se do Správce pověření Windows, "
        "ne do nastavení aplikace.",
        "en": "Jooble, Careerjet and Google Maps work only with your own key. "
        "You get the key after signing up on their site. It is kept in the Windows Credential "
        "Manager, not in the app settings.",
    },
    "keys.key": {"cs": "Klíč {source}", "en": "{source} key"},
    "keys.get": {"cs": "Získat zdarma", "en": "Get one for free"},
    "keys.saved": {"cs": "klíč uložen (…{end})", "en": "key saved (…{end})"},
    "keys.remove": {"cs": "Smazat klíč", "en": "Delete the key"},
    "keys.invalid": {
        "cs": "Klíč {source} vypadá neplatně: 8 až 200 písmen, číslic nebo pomlček.",
        "en": "The {source} key looks wrong: 8 to 200 letters, digits or dashes.",
    },
    "keys.save": {"cs": "Uložit klíče", "en": "Save the keys"},
    "sources.kind.api": {"cs": "S vlastním klíčem", "en": "With your own key"},
    "sources.no_key": {
        "cs": "chybí klíč – zadejte ho v Nastavení",
        "en": "no key – enter it in Settings",
    },
    "wizard.auto_on": {
        "cs": "Automatické hledání je zapnuté: každých {h} h.",
        "en": "Automatic checks are on: every {h} h.",
    },
    "tray.open": {"cs": "Otevřít", "en": "Open"},
    "tray.auto": {"cs": "Automatické hledání", "en": "Automatic checks"},
    "tray.pause": {"cs": "Pozastavit na 24 h", "en": "Pause for 24 h"},
    "tray.resume": {"cs": "Pokračovat v automatickém hledání", "en": "Resume automatic checks"},
    "tray.quit": {"cs": "Ukončit", "en": "Quit"},
    "notify.new": {"cs": "Nové nabídky: {n}", "en": "New jobs: {n}"},
    "notify.saved": {"cs": "{name}: nové nabídky {n}", "en": "{name}: {n} new"},
    "notify.social": {"cs": "{source}: {reason}", "en": "{source}: {reason}"},
    # Saved searches and export
    "saved.title": {"cs": "Uložená hledání", "en": "Saved searches"},
    "saved.save": {"cs": "Uložit hledání", "en": "Save search"},
    "saved.prompt": {"cs": "Název hledání", "en": "Name of the search"},
    "saved.default_name": {"cs": "Moje hledání", "en": "My search"},
    "saved.new": {"cs": "nové: {n}", "en": "new: {n}"},
    "saved.none": {
        "cs": "Zatím žádná. Nastavte filtry a klikněte na „Uložit hledání“.",
        "en": "None yet. Set the filters and click “Save search”.",
    },
    "saved.notify_on": {
        "cs": "Upozorňovat na nové nabídky (zapnuto)",
        "en": "Notify about new jobs (on)",
    },
    "saved.notify_off": {
        "cs": "Upozorňovat na nové nabídky (vypnuto)",
        "en": "Notify about new jobs (off)",
    },
    "saved.delete": {"cs": "Smazat", "en": "Delete"},
    "saved.full": {
        "cs": "Uložit lze nejvýš {n} hledání.",
        "en": "At most {n} searches can be saved.",
    },
    "export.label": {"cs": "Stáhnout seznam", "en": "Download the list"},
    "export.title": {"cs": "Pozice", "en": "Position"},
    "export.employer": {"cs": "Zaměstnavatel", "en": "Employer"},
    "export.place": {"cs": "Místo", "en": "Place"},
    "export.kraj": {"cs": "Kraj", "en": "Region"},
    "export.salary": {"cs": "Mzda", "en": "Salary"},
    "export.contract": {"cs": "Úvazek", "en": "Contract"},
    "export.shifts": {"cs": "Směny", "en": "Shifts"},
    "export.education": {"cs": "Vzdělání", "en": "Education"},
    "export.posted": {"cs": "Vloženo", "en": "Posted"},
    "export.sources": {"cs": "Zdroje", "en": "Sources"},
    "export.link": {"cs": "Odkaz", "en": "Link"},
    # Commute (PLAN §4.6)
    "sort.travel": {"cs": "Nejkratší cesta", "en": "Shortest commute"},
    "wizard.depart": {"cs": "Obvyklý odjezd do práce", "en": "Usual departure to work"},
    "wizard.depart.hint": {
        "cs": "Ve všední den; podle něj se počítají spoje MHD a vlaků.",
        "en": "On a weekday; public transport connections are computed for this time.",
    },
    "commute.title": {"cs": "Jak se tam dostanu", "en": "How to get there"},
    "commute.open": {"cs": "Jak se tam dostanu", "en": "How to get there"},
    "commute.close": {"cs": "Zavřít", "en": "Close"},
    "commute.from": {"cs": "Odkud dojíždíte", "en": "Commuting from"},
    "commute.mode.any": {"cs": "Čímkoli", "en": "Any way"},
    "commute.mode.car": {"cs": "Autem", "en": "By car"},
    "commute.mode.transit": {"cs": "MHD a vlakem", "en": "By public transport"},
    "commute.mode.walk": {"cs": "Pěšky", "en": "On foot"},
    "commute.mode.bike": {"cs": "Na kole", "en": "By bike"},
    "commute.within": {"cs": "do", "en": "within"},
    "commute.no_limit": {"cs": "bez omezení", "en": "no limit"},
    "commute.depart": {"cs": "Odjezd", "en": "Departure"},
    "commute.no_start": {
        "cs": "Zadejte, odkud dojíždíte, a uvidíte dobu cesty.",
        "en": "Enter where you commute from to see travel times.",
    },
    "commute.estimate": {
        "cs": "Doba cesty je zatím odhad",
        "en": "Travel times are estimates for now",
    },
    "commute.estimate_short": {"cs": "odhad", "en": "estimate"},
    "commute.no_map": {
        "cs": "Doba cesty je odhad vzdušnou čarou; přesné časy a trasy dá offline mapa.",
        "en": "Travel times are straight-line estimates; the offline map makes them exact.",
    },
    "commute.computing": {
        "cs": "Počítám přesné doby cesty…",
        "en": "Computing exact travel times…",
    },
    "commute.too_far": {"cs": "dál než 2 hodiny", "en": "more than 2 hours"},
    "commute.unknown": {
        "cs": "Toto místo neznám. Zkuste název obce.",
        "en": "Unknown place. Try the name of a town.",
    },
    "commute.missing_start": {
        "cs": "Nejdřív zadejte, odkud dojíždíte (pole nad seznamem).",
        "en": "First enter where you commute from (the box above the list).",
    },
    "commute.missing_place": {
        "cs": "U této nabídky není známé místo, takže cestu nelze spočítat.",
        "en": "This ad has no known place, so the route cannot be computed.",
    },
    "commute.depart_or_arrive": {"cs": "Odjezd nebo příjezd", "en": "Departure or arrival"},
    "commute.leave_at": {"cs": "Odjezd v", "en": "Leave at"},
    "commute.arrive_by": {"cs": "Příjezd do", "en": "Arrive by"},
    "commute.next_workday": {"cs": "nejbližší pracovní den", "en": "next working day"},
    "commute.transfers": {"cs": "přestupy: {n}", "en": "transfers: {n}"},
    "commute.walk_leg": {"cs": "pěšky {m} min", "en": "walk {m} min"},
    "commute.towards": {"cs": "směr", "en": "towards"},
    "commute.failed": {
        "cs": "Trasu se nepodařilo spočítat. Zkuste to za chvíli znovu.",
        "en": "The route could not be computed. Try again in a moment.",
    },
    "commute.no_route": {
        "cs": "V tuto dobu nenacházím žádné spojení.",
        "en": "No connection found at this time.",
    },
    "commute.estimate_minutes": {"cs": "Odhadem asi {m} min.", "en": "Roughly {m} min."},
    "commute.estimate_why": {
        "cs": "Přesné trasy budou k dispozici, až se připraví mapy a jízdní řády.",
        "en": "Exact routes will be available once the maps and timetables are ready.",
    },
    "commute.open_in": {"cs": "Otevřít v:", "en": "Open in:"},
    "commute.area": {"cs": "Kam se dostanu za", "en": "Where I can get within"},
    "commute.start_here": {"cs": "Dojíždět odsud", "en": "Commute from here"},
    "commute.attribution": {
        "cs": "Jízdní řády: Spojenka (CIS JŘ a další, jen pro nekomerční použití). "
        "Mapa: © přispěvatelé OpenStreetMap. Výpočet tras: MOTIS.",
        "en": "Timetables: Spojenka (CIS JŘ and others, non-commercial use only). "
        "Map: © OpenStreetMap contributors. Routing: MOTIS.",
    },
    "routing.title": {"cs": "Mapy a jízdní řády", "en": "Maps and timetables"},
    "routing.step.download_osm": {"cs": "Stahuji mapu", "en": "Downloading the map"},
    "routing.step.download_gtfs": {"cs": "Stahuji jízdní řády", "en": "Downloading timetables"},
    "routing.step.clip": {"cs": "Vyřezávám okolí domova", "en": "Cutting out the home area"},
    "routing.step.timetable": {"cs": "Připravuji jízdní řády", "en": "Preparing timetables"},
    "routing.step.import": {"cs": "Připravuji vyhledávání tras", "en": "Preparing route search"},
    "routing.not_ready": {
        "cs": "Zatím nejsou připravené; doba cesty bude k dispozici, až budou.",
        "en": "Not ready yet; travel times will be available once they are.",
    },
    "routing.not_downloaded": {"cs": "Nestaženo", "en": "Not downloaded"},
    "routing.download": {"cs": "Stáhnout mapu", "en": "Download the map"},
    "routing.download.text": {
        "cs": "Doba cesty do práce, filtr podle dojezdu a trasy potřebují offline mapu a jízdní "
        "řády. Stáhne se asi 1 GB a pak se na pozadí několik minut připravují; aplikaci "
        "můžete mezitím používat. Bez mapy jsou tyto funkce vypnuté.",
        "en": "Travel times, the commute filter and routes need the offline map and "
        "timetables. About 1 GB is downloaded, then prepared in the background for a few "
        "minutes; you can use the app meanwhile. Without the map these features are off.",
    },
    "routing.needs_home": {
        "cs": "Pro okolí domova nejdřív zadejte, odkud dojíždíte (Profil).",
        "en": "For the home area, first enter where you commute from (Profile).",
    },
    "routing.ready": {"cs": "Připraveno", "en": "Ready"},
    "routing.failed": {"cs": "Poslední aktualizace selhala", "en": "The last update failed"},
    "routing.area": {"cs": "Oblast", "en": "Area"},
    "routing.area.auto": {"cs": "Podle počítače", "en": "Depending on this PC"},
    "routing.area.whole": {"cs": "celá Česká republika", "en": "all of Czechia"},
    "routing.area.home": {"cs": "okolí domova (150 km)", "en": "home area (150 km)"},
    "routing.area.home_km": {"cs": "okolí domova ({km} km)", "en": "home area ({km} km)"},
    "routing.area.hint": {
        "cs": "Celá republika potřebuje asi 6 GB paměti při přípravě a 5 GB na disku; "
        "okolí domova jen zlomek.",
        "en": "All of Czechia needs about 6 GB of memory to prepare and 5 GB of disk; "
        "the home area only a fraction.",
    },
    "routing.update_now": {"cs": "Aktualizovat teď", "en": "Update now"},
    "routing.osm_at": {"cs": "Mapa stažena", "en": "Map downloaded"},
    "routing.gtfs_at": {"cs": "Jízdní řády staženy", "en": "Timetables downloaded"},
    "routing.imported_at": {"cs": "Připraveno", "en": "Prepared"},
    # --- Employers (PLAN §4.5) ---
    "sort.employer": {"cs": "Nejlépe hodnocený zaměstnavatel", "en": "Best-rated employer"},
    "filters.employer": {"cs": "Zaměstnavatel", "en": "Employer"},
    "filters.emp_min": {"cs": "Spokojenost zaměstnanců", "en": "Employee satisfaction"},
    "filters.and_more": {"cs": "a víc", "en": "or more"},
    "filters.emp_ok": {"cs": "Bez úředních varování", "en": "No official warnings"},
    "filters.emp_verified": {
        "cs": "Jen ověření zaměstnavatelé (s IČO)",
        "en": "Verified employers only (with IČO)",
    },
    "filters.emp_licensed": {
        "cs": "Skrýt agentury bez povolení",
        "en": "Hide agencies without a licence",
    },
    "filters.employer_only": {"cs": "Jen nabídky firmy", "en": "Only jobs of"},
    "filters.employer_clear": {"cs": "zrušit", "en": "clear"},
    "keys.google": {
        "cs": "Google Maps (hodnocení zákazníků)",
        "en": "Google Maps (customer ratings)",
    },
    "keys.google_hint": {
        "cs": "Nepovinné: hodnocení provozoven od zákazníků na kartě zaměstnavatele. Google "
        "vyžaduje platební kartu, aplikace ale zůstane v bezplatném limitu: tento měsíc "
        "{used} z {free} dotazů.",
        "en": "Optional: customer ratings of shops and branches on employer cards. Google asks "
        "for a payment card, but the app stays within the free limit: {used} of {free} "
        "requests this month.",
    },
    "employers.title": {"cs": "Zaměstnavatelé", "en": "Employers"},
    "employers.text": {
        "cs": "Při otevření karty zaměstnavatele se ověří úřední údaje (obchodní rejstřík, "
        "insolvence, DPH, agentury práce) a stáhnou recenze a zmínky na internetu.",
        "en": "When you open an employer card, official data (business register, insolvency, "
        "VAT, employment agencies) is checked and reviews and web mentions are fetched.",
    },
    "employers.auto": {
        "cs": "Ověřovat zaměstnavatele automaticky po každém hledání",
        "en": "Check employers automatically after each search",
    },
    "employers.auto.hint": {
        "cs": "Na pozadí ověří úřední údaje všech zaměstnavatelů a stáhne recenze u nabídek, "
        "které odpovídají vašemu profilu.",
        "en": "Checks the official data of all employers in the background and fetches "
        "reviews for jobs that match your profile.",
    },
    "employers.auto.on": {"cs": "automaticky", "en": "automatically"},
    "employers.auto.off": {"cs": "při otevření karty", "en": "when a card is opened"},
    "employers.sources": {"cs": "Recenze a zmínky", "en": "Reviews and mentions"},
    "employers.source.atmoskop": {
        "cs": "Atmoskop (recenze zaměstnanců)",
        "en": "Atmoskop (employee reviews)",
    },
    "employers.source.atmoskop.hint": {
        "cs": "Spokojenost v % a texty recenzí; v pozadí nanejvýš 150 firem denně.",
        "en": "Satisfaction in % and review texts; at most 150 companies a day in the background.",
    },
    "employers.source.mentions": {"cs": "Zmínky na internetu", "en": "Web mentions"},
    "employers.source.mentions.hint": {
        "cs": "Vyhledávání zkušeností a stížností a čtení nalezených stránek; v pozadí "
        "nanejvýš 20 firem denně.",
        "en": "Searches for experiences and complaints and reads the pages found; at most 20 "
        "companies a day in the background.",
    },
    "employers.source.google": {
        "cs": "Google Maps (hodnocení zákazníků)",
        "en": "Google Maps (customer ratings)",
    },
    "employers.source.google.hint": {
        "cs": "Jen s vlastním klíčem (Nastavení → Klíče k dalším službám).",
        "en": "Only with your own key (Settings → Keys for other services).",
    },
    "employers.source.facebook": {
        "cs": "Facebook (stránka firmy)",
        "en": "Facebook (company page)",
    },
    "employers.source.facebook.hint": {
        "cs": "Odkaz na stránku firmy, když ji vyhledávání najde.",
        "en": "A link to the company page when the search finds it.",
    },
    "employers.stats": {
        "cs": "Zaměstnavatelů v nabídkách: {total}, s IČO {resolved}, ověřeno {checked}, "
        "s varováním {warned}, s recenzemi {reviewed}.",
        "en": "Employers in the jobs: {total}, with IČO {resolved}, checked {checked}, "
        "with warnings {warned}, with reviews {reviewed}.",
    },
    "employers.phase.link": {"cs": "Přiřazuji nabídky", "en": "Linking jobs"},
    "employers.phase.resolve": {"cs": "Hledám firmy v rejstříku", "en": "Finding companies"},
    "employers.phase.check": {"cs": "Ověřuji v rejstřících", "en": "Checking registers"},
    "employers.phase.reviews": {"cs": "Stahuji recenze", "en": "Fetching reviews"},
    "employers.phase.summaries": {"cs": "Píšu shrnutí", "en": "Writing summaries"},
    "employers.phase.done": {"cs": "Hotovo", "en": "Done"},
    "employers.phase.failed": {"cs": "Selhalo", "en": "Failed"},
    "employer.title": {"cs": "Karta zaměstnavatele", "en": "Employer card"},
    "employer.more": {"cs": "Více o zaměstnavateli", "en": "More about the employer"},
    "filters.employer_gone": {
        "cs": "(už není v seznamu)",
        "en": "(no longer listed)",
    },
    "employer.gone": {
        "cs": "Tento zaměstnavatel už není v seznamu.",
        "en": "This employer is no longer listed.",
    },
    "employer.as_ads": {"cs": "v inzerátech:", "en": "in the ads:"},
    "employer.step.check": {"cs": "Ověřuji v rejstřících", "en": "Checking the registers"},
    "employer.step.resolve": {
        "cs": "Hledám, o kterou firmu jde",
        "en": "Finding out which company it is",
    },
    "employer.step.reviews": {"cs": "Stahuji recenze", "en": "Fetching reviews"},
    "employer.step.mentions": {"cs": "Hledám zmínky na internetu", "en": "Searching the web"},
    "employer.step.summary": {
        "cs": "Umělá inteligence píše shrnutí",
        "en": "The AI is writing a summary",
    },
    "employer.uncertain": {
        "cs": "Nejisté přiřazení: jménu z inzerátu odpovídá víc firem. Vyberte tu správnou.",
        "en": "Uncertain match: several companies fit the name in the ad. Pick the right one.",
    },
    "employer.unknown": {
        "cs": "Firmu se nepodařilo dohledat v obchodním rejstříku. Znáte její IČO?",
        "en": "The company was not found in the business register. Do you know its IČO?",
    },
    "employer.people": {"cs": "zaměstnanců", "en": "employees"},
    "employer.this_one": {"cs": "Je to tato", "en": "This one"},
    "employer.checks": {"cs": "Úřední rejstříky", "en": "Official registers"},
    "employer.no_ico": {
        "cs": "Bez IČO nelze ověřit v úředních rejstřících.",
        "en": "Without an IČO the official registers cannot be checked.",
    },
    "employer.checking": {"cs": "Ověřuje se", "en": "Being checked"},
    "employer.ended": {"cs": "Firma zanikla {date}", "en": "The company ceased on {date}"},
    "employer.founded": {
        "cs": "V obchodním rejstříku od {date}, činná",
        "en": "Registered since {date}, active",
    },
    "employer.insolvency": {"cs": "Insolvenční řízení", "en": "Insolvency proceedings"},
    "employer.since": {"cs": "od", "en": "since"},
    "employer.no_insolvency": {"cs": "Bez insolvence", "en": "No insolvency"},
    "employer.vat_unreliable": {
        "cs": "Nespolehlivý plátce DPH (od {date})",
        "en": "Unreliable VAT payer (since {date})",
    },
    "employer.vat_ok": {"cs": "Spolehlivý plátce DPH", "en": "Reliable VAT payer"},
    "employer.no_vat": {"cs": "Není plátce DPH", "en": "Not a VAT payer"},
    "employer.agency": {"cs": "Agentura práce s povolením", "en": "Licensed employment agency"},
    "employer.agency_temp": {
        "cs": "Agentura práce s povolením i k agenturnímu zaměstnávání",
        "en": "Employment agency licensed for temporary staffing",
    },
    "employer.until": {"cs": "do {date}", "en": "until {date}"},
    "employer.unlicensed": {
        "cs": "Inzeráty mluví o agenturní práci, ale firma není v registru agentur práce",
        "en": "The ads speak of agency work, but the company is not a licensed agency",
    },
    "employer.seat": {"cs": "sídlo", "en": "registered office"},
    "employer.checked": {
        "cs": "Ověřeno {date} (ARES, ISIR, registr DPH, registr agentur MPSV).",
        "en": "Checked on {date} (ARES, ISIR, VAT register, MPSV agency register).",
    },
    "employer.match.ares": {
        "cs": "přiřazeno podle jména v rejstříku",
        "en": "matched by name in the register",
    },
    "employer.match.ai": {"cs": "přiřadila umělá inteligence", "en": "matched by the AI"},
    "employer.match.brand": {"cs": "přiřazeno podle značky", "en": "matched by brand"},
    "employer.match.agency": {
        "cs": "přiřazeno podle registru agentur",
        "en": "matched by the agency register",
    },
    "employer.wrong": {"cs": "Je to jiná firma?", "en": "A different company?"},
    "employer.fix": {"cs": "Opravit podle IČO", "en": "Fix by IČO"},
    "employer.employees": {"cs": "Co říkají zaměstnanci", "en": "What employees say"},
    "employer.satisfaction": {
        "cs": "spokojenost se zaměstnavatelem ({n} hodnocení)",
        "en": "satisfied with the employer ({n} ratings)",
    },
    "employer.colleagues": {"cs": "kolegové", "en": "colleagues"},
    "employer.boss": {"cs": "nadřízený", "en": "boss"},
    "employer.suitable": {"cs": "vhodné pro:", "en": "good for:"},
    "employer.no_atmoskop": {
        "cs": "Na Atmoskopu zatím nemá hodnocení.",
        "en": "No ratings on Atmoskop yet.",
    },
    "employer.summary": {"cs": "Shrnutí od AI", "en": "AI summary"},
    "employer.summary_note": {
        "cs": "z recenzí a zmínek; čísla vedou na zdroj. Může se mýlit.",
        "en": "from the reviews and mentions; the numbers link to the source. It can be wrong.",
    },
    "employer.warnings": {"cs": "Varování", "en": "Warnings"},
    "employer.praise": {"cs": "Co chválí", "en": "What people praise"},
    "employer.criticism": {"cs": "Co kritizují", "en": "What people criticize"},
    "employer.reviews": {"cs": "Recenze ({n})", "en": "Reviews ({n})"},
    "employer.current": {"cs": "současný zaměstnanec", "en": "current employee"},
    "employer.customers": {"cs": "Co říkají zákazníci", "en": "What customers say"},
    "employer.google": {"cs": "({n} hodnocení)", "en": "({n} ratings)"},
    "employer.customers_note": {
        "cs": "Hodnocení zákazníků provozovny, ne zaměstnanců.",
        "en": "Ratings by customers of the place, not by employees.",
    },
    "employer.google_key": {
        "cs": "Hodnocení zákazníků z Google Map se ukáže po zadání klíče v",
        "en": "Customer ratings from Google Maps appear once you enter a key in",
    },
    "employer.no_google": {
        "cs": "Google Maps provozovnu nenašly.",
        "en": "Google Maps found no place.",
    },
    "employer.web": {"cs": "Zmínky na internetu", "en": "Mentions online"},
    "employer.no_mentions": {
        "cs": "Žádné zmínky o práci u této firmy.",
        "en": "No mentions of working there.",
    },
    "employer.link.atmoskop": {"cs": "Atmoskop", "en": "Atmoskop"},
    "employer.link.indeed": {"cs": "Indeed", "en": "Indeed"},
    "employer.link.google": {"cs": "Google Maps", "en": "Google Maps"},
    "employer.link.mapy": {"cs": "Mapy.cz", "en": "Mapy.cz"},
    "employer.link.facebook": {"cs": "Facebook", "en": "Facebook"},
    "employer.link.ares": {"cs": "ARES", "en": "ARES"},
    "employer.link.justice": {"cs": "Obchodní rejstřík", "en": "Business register"},
    "employer.link.isir": {"cs": "Insolvenční rejstřík", "en": "Insolvency register"},
    "employer.all_jobs": {"cs": "Všechny nabídky ({n})", "en": "All jobs ({n})"},
    "employer.refresh": {"cs": "Aktualizovat", "en": "Refresh"},
    "employer.reviews_at": {"cs": "Recenze {date}", "en": "Reviews {date}"},
    "employer.mentions_at": {"cs": "zmínky {date}", "en": "mentions {date}"},
    "employer.summary_at": {"cs": "shrnutí {date} ({model})", "en": "summary {date} ({model})"},
    "employer.badge_satisfaction": {
        "cs": "Spokojenost zaměstnanců (Atmoskop)",
        "en": "Employee satisfaction (Atmoskop)",
    },
    "employer.for_client": {"cs": "pro firmu {name}", "en": "for {name}"},
    "employer.badge_warning": {"cs": "Úřední varování", "en": "Official warning"},
    "employer.warning.ended": {"cs": "zaniklá firma", "en": "company ceased"},
    "employer.warning.insolvency": {"cs": "insolvence", "en": "insolvency"},
    "employer.warning.vat": {"cs": "nespolehlivý plátce DPH", "en": "unreliable VAT payer"},
    "employer.warning.unlicensed": {"cs": "agentura bez povolení?", "en": "unlicensed agency?"},
    "employer.warning.not_found": {"cs": "IČO není v ARES", "en": "IČO not in ARES"},
    "employer.not_in_ares": {
        "cs": "IČO z inzerátu v obchodním rejstříku (ARES) není.",
        "en": "The IČO from the ad is not in the business register (ARES).",
    },
    "employer.badge_guess": {
        "cs": "Odhad z textu inzerátů, ne úřední údaj",
        "en": "A guess from the ads' text, not an official record",
    },
    "employer.not_checked": {"cs": "Zatím neověřeno.", "en": "Not checked yet."},
    "employer.isir.pending": {"cs": "návrh se projednává", "en": "petition being decided"},
    "employer.isir.moratorium": {"cs": "moratorium", "en": "moratorium"},
    "employer.isir.bankrupt": {"cs": "zjištěn úpadek", "en": "declared insolvent"},
    "employer.isir.konkurs": {"cs": "konkurs", "en": "bankruptcy"},
    "employer.isir.debt_relief": {"cs": "oddlužení", "en": "debt relief"},
    "employer.isir.reorganization": {"cs": "reorganizace", "en": "reorganization"},
    "employer.isir.ended": {"cs": "řízení skončilo", "en": "proceedings ended"},
    "employer.isir.cancelled": {"cs": "řízení zrušeno", "en": "proceedings cancelled"},
    "employer.isir.error": {"cs": "chybný zápis", "en": "wrong entry"},
}


def translate(lang: str, key: str, **kwargs: object) -> str:
    entry = TEXTS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry["cs"]
    return text.format(**kwargs) if kwargs else text
