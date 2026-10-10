"""The ~20-second speed test after a model download (PLAN §6.6 step 3).

Runs the real extraction prompt on a built-in sample ad, so the measured prompt and output
speeds match the work the model will do. The result replaces the table estimate.
"""

from adawork.ai.advisor import Speed
from adawork.ai.client import LlmClient, Timing
from adawork.ai.extract import extract
from adawork.ai.isco import Candidate

SAMPLE_AD = """Skladník / skladnice – ranní směna
Logistika Polabí s.r.o.
Průmyslová 1520, Kolín
32 000 – 38 000 Kč/měsíc

Hledáme do našeho moderního skladu v průmyslové zóně Kolín-Ovčáry nové kolegy.

Co vás čeká:
• příjem a výdej zboží, kontrola dodávek podle dodacích listů
• vychystávání objednávek pomocí čtečky čárových kódů
• obsluha vysokozdvižného vozíku (VZV) – průkaz výhodou, zaškolíme
• udržování pořádku ve skladu

Co požadujeme:
• vyučení v jakémkoli oboru
• spolehlivost a chuť pracovat v týmu
• ochotu pracovat ve dvousměnném provozu (6–14, 14–22)

Co nabízíme:
• hlavní pracovní poměr na dobu neurčitou po zkušební době
• mzdu 32 000 – 38 000 Kč měsíčně + čtvrtletní prémie
• stravenky v hodnotě 120 Kč, příspěvek na dopravu
• 25 dní dovolené, firemní akce
• nástup možný ihned

Životopis posílejte přes formulář, ozveme se do tří dnů."""

SAMPLE_CANDIDATES = [
    Candidate("4321", "Úředníci ve skladech", 3.0, ("Skladník", "Skladová účetní")),
    Candidate("9333", "Pomocní pracovníci v oblasti dopravy a skladování", 2.5, ("Skladník",)),
    Candidate("8344", "Obsluha vysokozdvižných a jiných vozíků", 1.0, ("Řidič VZV",)),
]
ROUNDS = 2


def run(client: LlmClient) -> Speed:
    """Two extractions: the first reads the whole prompt (no cache), the second shows the
    usual case where the shared instructions are already cached."""
    t = Timing.total([extract(client, SAMPLE_AD, SAMPLE_CANDIDATES).timing for _ in range(ROUNDS)])
    return Speed(prompt_tps=t.prompt_tps or 1.0, output_tps=t.output_tps or 1.0, measured=True)
