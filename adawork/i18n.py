"""UI texts. Czech is the default; English is selectable in the wizard and Settings."""

TEXTS: dict[str, dict[str, str]] = {
    "nav.search": {"cs": "Hledat", "en": "Search"},
    "nav.wizard": {"cs": "Průvodce nastavením", "en": "Setup wizard"},
    "nav.sources": {"cs": "Zdroje", "en": "Sources"},
    "wizard.title": {"cs": "Průvodce nastavením", "en": "Setup wizard"},
    "wizard.step": {"cs": "Krok {n} ze {total}", "en": "Step {n} of {total}"},
    "wizard.next": {"cs": "Další", "en": "Next"},
    "wizard.back": {"cs": "Zpět", "en": "Back"},
    "wizard.welcome": {"cs": "Vítejte", "en": "Welcome"},
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
    "wizard.done": {"cs": "Hotovo", "en": "Done"},
    "wizard.done.text": {
        "cs": "Po kliknutí na Start stáhneme nabídky z úřadu práce (všechny obory, asi minuta) "
        "a potom z vybraných portálů pro kraje ve vašem dosahu. Portály čteme pomalu, aby "
        "nás nezablokovaly, takže to může trvat i desítky minut. Nabídky přibývají průběžně.",
        "en": "Start downloads job offers from the labour office (every field, about a "
        "minute), then from the chosen portals for the regions within your reach. Portals "
        "are read slowly so they do not block us, which can take tens of minutes. Jobs "
        "appear as they come in.",
    },
    "wizard.sources": {"cs": "Zdroje nabídek", "en": "Job sources"},
    "sources.title": {"cs": "Zdroje nabídek", "en": "Job sources"},
    "sources.text": {
        "cs": "Vyberte, odkud má aplikace nabídky stahovat.",
        "en": "Choose where the app downloads job offers from.",
    },
    "sources.portals": {"cs": "Úřad práce a portály", "en": "Labour office and portals"},
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
        "cs": "LinkedIn se zapne, až zaškrtnete, že rozumíte riziku.",
        "en": "LinkedIn is switched on only after you tick that you understand the risk.",
    },
    "sources.indeed": {
        "cs": "Indeed automatické čtení blokuje. Ve vyhledávání najdete odkaz, který stejné "
        "hledání otevře na Indeedu ve vašem prohlížeči.",
        "en": "Indeed blocks automatic reading. The search page has a link that opens the same "
        "search on Indeed in your browser.",
    },
    "sources.social": {
        "cs": "Facebook a Instagram přibudou v pozdější verzi (ve výchozím stavu vypnuté).",
        "en": "Facebook and Instagram come in a later version (off by default).",
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
    "nav.ai": {"cs": "Umělá inteligence", "en": "AI"},
    "wizard.ai": {"cs": "Umělá inteligence", "en": "Artificial intelligence"},
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
    "wizard.auto_off": {
        "cs": "Automatické hledání je vypnuté. Zapnout ho můžete později v Nastavení.",
        "en": "Automatic checks are off. You can switch them on later in Settings.",
    },
    "wizard.start": {"cs": "Start", "en": "Start"},
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
    "job.no_text": {
        "cs": "Inzerát nemá popis. Celý text najdete v původní nabídce.",
        "en": "The ad has no description. The full text is in the original ad.",
    },
    "job.close": {"cs": "Zavřít", "en": "Close"},
    "page.top": {"cs": "Nahoru", "en": "Back to top"},
    "scrape.button": {"cs": "Obnovit nabídky", "en": "Refresh jobs"},
    "scrape.button_hint": {
        "cs": "Ověří, zda nabídky v seznamu stále platí, a doplní nové nad ně.",
        "en": "Checks the jobs in the list are still open and adds the new ones above them.",
    },
    "scrape.download": {"cs": "Stahuji data…", "en": "Downloading…"},
    "scrape.import": {"cs": "Zpracovávám nabídky: {n}", "en": "Processing jobs: {n}"},
    "scrape.geocode": {"cs": "Určuji polohu nabídek…", "en": "Locating jobs…"},
    "scrape.listing": {
        "cs": "Procházím {source}: {n} nabídek",
        "en": "Browsing {source}: {n} jobs",
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
        "cs": "Smaže všechny nalezené nabídky a najde je znovu.",
        "en": "Deletes all found jobs and finds them again.",
    },
    "scrape.fresh_confirm": {
        "cs": "Smazat všechny nalezené nabídky a hledat znovu?",
        "en": "Delete all found jobs and search again?",
    },
    "scrape.done": {
        "cs": "Hotovo: {new} nových, {updated} změněných, {removed} již neplatných.",
        "en": "Done: {new} new, {updated} changed, {removed} no longer open.",
    },
    "scrape.failed": {"cs": "Stahování selhalo: {msg}", "en": "Download failed: {msg}"},
    "status.jobs": {"cs": "Aktivní nabídky: {n}", "en": "Active jobs: {n}"},
    "status.last_run": {"cs": "poslední aktualizace {when}", "en": "last updated {when}"},
    "status.auto_off": {"cs": "automatické hledání vypnuto", "en": "automatic checks off"},
    "status.auto_on": {"cs": "automaticky každých {h} h", "en": "automatically every {h} h"},
}


def translate(lang: str, key: str, **kwargs: object) -> str:
    entry = TEXTS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry["cs"]
    return text.format(**kwargs) if kwargs else text
