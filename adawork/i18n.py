"""UI texts. Czech is the default; English is selectable in the wizard and Settings."""

TEXTS: dict[str, dict[str, str]] = {
    "nav.search": {"cs": "Hledat", "en": "Search"},
    "nav.wizard": {"cs": "Průvodce nastavením", "en": "Setup wizard"},
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
        "cs": "Po kliknutí na Start stáhneme nabídky z úřadu práce (všechny obory, asi minuta). "
        "Další zdroje přibudou v příštích verzích.",
        "en": "Start downloads job offers from the labour office (every field, about a "
        "minute). More sources will follow in later versions.",
    },
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
