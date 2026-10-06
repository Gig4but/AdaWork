# M0 spike: employer data sources

Date: 2026-10-06. All sources were tested live from Windows 11 with Python 3.12, uv and httpx.
Scripts are in `spikes/employers/` (PEP 723, run with `uv run spikes/employers/<name>.py`).
Fixtures are in `tests/fixtures/employers/<source>/`. Requests were spaced 1.5–8 s apart per host,
and the whole spike sent roughly 60 requests in total.

## Summary table

| Source | Works? | Endpoint | Auth | Limits / terms | Fields we get | Python approach | Fixtures |
|---|---|---|---|---|---|---|---|
| **ARES**: detail by IČO | ✅ | `GET https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}` | none | MF: blocking possible above **500 req/min**, also for repeated identical or bad queries, parallel floods, or spreading load over many IPs. No rate-limit headers are sent. | name, sídlo (structured + RÚIAN codes), pravniForma, datumVzniku/Zaniku, **dic**, czNace (2025) + czNace2008 lists (unordered), **seznamRegistraci** (status per register, incl. `stavZdrojeIr` = insolvency, `stavZdrojeDph`, `stavZdrojeVr`) | httpx JSON | `ares/subjekt_00177041_skoda.json`, `ares/subjekt_01493248_in_insolvency.json`, `ares/subjekt_not_found.json` |
| **ARES**: name search | ✅ (with caveats) | `POST …/rest/ekonomicke-subjekty/vyhledat`, body `{"obchodniJmeno": "...", "sidlo": {"kodObce": n} \| {"textovaAdresa": "..."}, "pravniForma": ["112"], "czNace": [...], "start": 0, "pocet": 100}` | none | same; **≤1000 hits per query**, otherwise HTTP 400 `VYSTUP_PRILIS_MNOHO_VYSLEDKU`; `pocet` up to 1000 accepted | same shape as the detail record, plus `pocetCelkem` | httpx JSON | `ares/search_results_summary.json`, `ares/search_penny_market_raw.json` |
| **ARES RES**: statistics | ✅ | `GET …/rest/ekonomicke-subjekty-res/{ico}` or **batch** `POST …/ekonomicke-subjekty-res/vyhledat {"ico": [..up to 100..]}` | none | same | **kategoriePoctuPracovniku** (employee band, codebook `KategoriePoctuPracovniku`), **czNacePrevazujici** (main NACE), institutional sector, okres NUTS/LAU | httpx JSON | `ares/res_00177041_skoda.json`, `ares/res_batch_penny_candidates.json`, `ares/ciselnik_kategorie_poctu_pracovniku.json` |
| **ARES VR**: public register | ✅ | `GET …/rest/ekonomicke-subjekty-vr/{ico}` | none | same; large (Škoda 145 kB) | spisová značka, statutory bodies, capital, branches, activities, history | httpx JSON, only on demand | `ares/vr_00177041_skoda_trimmed.json` |
| **ISIR** (`ISIR_CUZK_WS`) | ✅ | SOAP `https://isir.justice.cz:8443/isir_cuzk_ws/IsirWsCuzkService` (WSDL `?wsdl`), operation `getIsirWsCuzkData` | none | MSp: blocking possible above **50 req/min or 3000 req/day**; data synced hourly | per proceeding: ic, name, address, court, spisová značka (INS bcVec/rocnik), **druhStavKonkursu** (KONKURS, ÚPADEK, ODDLUŽENÍ, …), urlDetailRizeni, datumPmZahajeniUpadku/UkonceniUpadku; status `WS2` = no record | raw SOAP via httpx + ElementTree (zeep also works, but is not needed) | `isir/isir_00177041_none.xml`, `isir/isir_01493248_debtor.xml` |
| **Unreliable VAT payer** (ADIS `rozhraniCRPDPH`) | ✅ | SOAP `https://adisrws.mfcr.cz/dpr/axis2/services/rozhraniCRPDPH.rozhraniCRPDPHSOAP` (the WSDL advertises `https://mojedane.gov.cz/dpr/...`) | none | ≤100 DIČ per call; MF: blocking possible above 10 000 req/day or 2 000 req/hour; daily outage 0:00–0:10 (status 2) | per DIČ: `nespolehlivyPlatce` ANO/NE/NENALEZEN, datumZverejneniNespolehlivosti, cisloFu, published bank accounts; V2 adds name, address, `typSubjektu` | raw SOAP via httpx; **daily bulk list** `getSeznamNespolehlivyPlatce` (505 kB, 4 335 entries, 2.7 s) and local lookup | `vat/*.xml` |
| **MPSV agency register** | ✅ | `GET https://data.mpsv.cz/od/soubory/agentury-prace/agentury-prace.json` (+ `.jsonld`, `.schema.json`) | none | open data, regenerated daily (has `Last-Modified` and `ETag`) | 1 912 agencies (1 781 distinct IČO, 124 without IČO): nazev, ico, address (RÚIAN ids), branches, contact persons, **povoleni[]** (platnostOd/Do, druhyPraci, zamestnavaniOsob, vyhledavaniZamestnani, …) | conditional GET of 4.0 MB JSON once a day, then an IČO → licence dict | `mpsv_agencies/agentury_prace_sample.json` |
| **Atmoskop.cz** | ✅ | sitemap `https://www.atmoskop.cz/sitemap.xml` (37 664 profiles); profile `…/nazory-na-zamestnavatele/<id>-<slug>`; reviews `…/hodnoceni`, `…/hodnoceni/2`, … | none | robots.txt disallows only `/graphql/`, `/nahlasit/`, login paths, and CCBot. The terms page (almacareer.legal) is JS-rendered and was not reviewed. Keep volume personal and low. | from `__NEXT_DATA__` (Apollo cache): countOfReviews, countOfComments, **employer/colleagues/boss satisfaction %**, top-3 "suitable for" tags, employees, address, field; reviews (10 per page): positive (`valueDiamond`), negative (`valueBulb`), month/year, tenure, department, current/former, votes, employer reply | httpx + regex/JSON (no HTML parsing) | `atmoskop/penny_market_*.html`, `atmoskop/manpowergroup_*.html` (+ `.expected.json`), `atmoskop/sitemap_sample.xml`, `atmoskop/robots.txt` |
| **Indeed company reviews** | ❌ (plain HTTP) | `https://cz.indeed.com/cmp/<slug>/reviews` | none | **HTTP 403, Cloudflare challenge** (`Cf-Mitigated: challenge`, "Security Check") for both `/cmp/X` and `/cmp/X/reviews`, with a browser UA or a custom UA. robots.txt allows `/cmp/*/reviews`. | none | a real browser would be needed (Playwright + Edge); not tested | `indeed/cmp_reviews_403_challenge.html`, `indeed/robots.txt` |
| **Google Places API (New)** | not tested (no key) | Text Search `POST https://places.googleapis.com/v1/places:searchText`; Details `GET https://places.googleapis.com/v1/places/{place_id}`; headers `X-Goog-Api-Key`, `X-Goog-FieldMask` | API key + **Cloud billing account (card)** | per-SKU free monthly caps since 2025-03: Text Search *IDs only* **unlimited free**; Text Search Pro 5 000; Text Search Enterprise 1 000 (then $35/1k); Place Details Enterprise (`rating`, `userRatingCount`) 1 000 (then $20/1k); Place Details **Enterprise + Atmosphere** (`reviews`) 1 000 (then $25/1k) | rating, userRatingCount, up to 5 reviews, googleMapsUri | httpx JSON | – |
| **Web mentions (ddgs 9.16.0)** | ✅ (flaky) | `DDGS().text(q, region="cz-cs", max_results=10)` | none | no published limits; observed **transient `DDGSException: No results found`** on 2 of 3 queries in a quick run, and success on retry 8 s later; `backend="duckduckgo"` alone always failed; `auto`/`bing` worked (~2 s per query) | title, href, body snippet | `ddgs` + retry/backoff + 30-day cache | `ddgs/text_results.json` |

## Per-source notes

### ARES
- Name search (`obchodniJmeno`) is a **word-based AND match**. It ignores case and diacritics
  ("Skoda Auto" = "ŠKODA AUTO"). There is **no prefix match** ("Kaufl" → 0) and **no
  abbreviation handling** ("Lidl ČR" → 0, "Lidl Česká republika" → 1 exact hit). Legal-form
  tokens in the query are harmless ("Penny Market, s. r. o." works).
- Results are **sorted by IČO, not by relevance**. With 529 hits for "Albert", the real chain is
  not on page 1, which is full of sole traders called Albert.
- Short brand queries return trade unions (pravní forma 707), associations (706), foundations
  (118) and housing associations (145) that contain the brand name. Example: "Penny" → 16 hits,
  "Penny Market" → 7 hits, of which 6 are unions. The legal form is the cheapest filter.
- The `sidlo` filter filters the **registered seat**, not the workplace. For chains the seat is
  in Prague ("Albert" + `textovaAdresa: "Kolín"` → only a sole trader). So the job location can be
  used as a ranking signal, never as a filter.
- The basic record has no employee count. **RES** has `kategoriePoctuPracovniku` (code 110 = none …
  470 = 5 000–9 999 … 510 = 10 000+) and `czNacePrevazujici`. The RES search accepts a **list of
  IČO**, so one call ranks all candidates. Penny Market s.r.o. = 470 / NACE 471; its unions = 000 /
  94200.
- **`seznamRegistraci.stavZdrojeIr`** shows whether the subject is in the insolvency register
  (`AKTIVNI` for the debtor Stavby Kohout, `NEEXISTUJICI` for Škoda). This is a free pre-check:
  call ISIR only when it is not `NEEXISTUJICI`, or when showing details. `stavZdrojeDph`
  (AKTIVNI/ZANIKLY) and `dic` feed the VAT check.
- Errors come back as JSON `{"kod": "NENALEZENO" | "CHYBA_VSTUPU", "popis", "subKod"}` with HTTP
  404/400.
- The OpenAPI spec is at `https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/v3/api-docs` (v1.4).

### ISIR
- There is one operation, `getIsirWsCuzkData`. Inputs: `ic`, `rc`, `druhVec`/`bcVec`/`rocnik`,
  `nazevOsoby` (prefix match, ≥2 characters), `jmeno`, `datumNarozeni`, `maxPocetVysledku`
  (default 200), `filtrAktualniRizeni` (T = only ongoing proceedings), `vyhledatPresnouShoduJmen`,
  `vyhledatBezDiakritiky`, `maxRelevanceVysledku`.
- **Gotchas found:**
  1. The XSD is a strict sequence. Elements must be sent in the documented order, or the
     service returns a SOAP Fault `cvc-complex-type.2.4.a`.
  2. **Empty elements are rejected.** Send only the filled ones.
- Responses: `stav.kodChyby` `WS2` = no record (the normal "clean" answer), `WS1` = bad parameter
  combination. Dates carry a `Z` suffix (`2023-11-14Z`). One IČO can have several proceedings.
  For the card, use `filtrAktualniRizeni=T`, or show all with their state.
- Recommended call: `ic=<IČO>`, `maxRelevanceVysledku=2` (match by IČO only), with a 30-day cache.
  ~150 lines of code, no zeep dependency.

### Unreliable VAT payer
- **Recommended: download the bulk list once a day** (`getSeznamNespolehlivyPlatce`, ~0.5 MB,
  4 335 DIČ, `bezVypisuUctu="ANO"`). Then look up DIČs locally. There are no per-employer calls,
  so the limits are irrelevant.
- The DIČ comes from ARES `dic` (`CZ` + digits). Send it **without** the `CZ` prefix. The DIČ is
  not always `CZ+IČO` (natural persons, VAT groups `dicSkDph`), so take it from ARES. A `NENALEZEN`
  status only means "not a VAT payer"; show it as neutral, not as a warning.
- `getStatusNespolehlivySubjektRozsirenyV2` also reports `NESPOLEHLIVA_OSOBA` (unreliable
  non-payer). This is useful for an on-demand refresh when the card is opened.

### MPSV agency register
- The data is **JSON, not XML** as PLAN.md says: `data.mpsv.cz/od/soubory/agentury-prace/agentury-prace.json`,
  4.0 MB, about 1.7 s to download.
- The dataset contains **only agencies with at least one valid permit** (stated in its
  methodology). An IČO missing from the list, combined with ad text that looks like an agency,
  therefore means "no valid licence".
- Of 3 019 permits, **2 570 have `platnostDo = null`** (no end date). "Licence valid until"
  is therefore often unknown. Show "platná" and add the date only when it is present.
- `zamestnavaniOsob=true` (1 088 permits) marks temp-staffing licences (agenturní zaměstnávání).
  This distinguishes temp staffing from pure placement.
- 124 records have no IČO (foreign entities). Match those by normalized name. 7 IČOs have more
  than one record, so merge their permits.
- Index: `{ico: {nazev, permits[], valid_until (max, or None if open-ended), temp_staffing}}`.
  Rebuild it when the `ETag` changes.

### Atmoskop
- The site is Next.js. Everything is in `<script id="__NEXT_DATA__">` → `props.pageProps.initialApolloState`.
  - `ROOT_QUERY["profileOverview({...})"]` holds the counts and satisfaction percentages.
  - `AbstractProfile:<id>` holds the name, address, employees and field.
  - `ReviewData:<id>` holds the reviews, referenced from `profileTextData(...).answers.currentPageItems`,
    with a paginator (`itemsCount`, `lastPage`).
- **There is no star rating.** Atmoskop publishes **percentage satisfaction** (employer,
  colleagues, boss) plus the number of ratings and comments. Penny Market: 861 ratings, 604
  comments, 62/71/62 %.
- The reviews tab shows only "confirmed" reviews by default (Penny: 380 of 604 comments).
  `?strana=2` and `?page=2` are ignored; **`/hodnoceni/2`** paginates.
- Each review has a positive text (`valueDiamond`), a negative text (`valueBulb`), the month and
  year it was written (`"srpen 2026"`), how long ago it was added (bucket), tenure, department,
  current/former employee, helpfulness votes and the employer's reply. The labels are i18n codes
  such as `OPTION_FIELD_PRODUCTION` and `OPTION_RECOMMEND_STABLE`, so we need a small cs mapping
  table.
- **No IČO on the page and no usable search.** `/seznam-firem?hledat=` ignores the query; the
  live search uses `/graphql/`, which robots.txt disallows.
  → **Use the sitemap as the index.** It has 37 664 URLs `<id>-<webalized legal name>` (6.7 MB
  uncompressed, ~0.6 MB gzip). `slugify(ARES obchodniJmeno)` matched **6/6** big employers
  exactly (Albert, Lidl, Kaufland, Škoda Auto, Penny Market, ManpowerGroup). Only 43 slugs are
  ambiguous. Refresh weekly.
- Page weight is ~360 kB uncompressed (~90 kB on the wire). One profile = 2 requests (overview +
  first reviews page).

### Indeed
- Plain HTTP is blocked by a Cloudflare managed challenge (403) even for the first request.
  Company slugs are not predictable either (Penny's page is `cmp/Penny-9`, found via ddgs).
- Options:
  - (a) drop Indeed reviews from v1 and show only the link (found by ddgs);
  - (b) fetch on demand in the Playwright/Edge session the app already plans (untested; it may
    still challenge).
- **Recommendation: (a) for v1**, (b) as a later experiment. We should not try to defeat the
  challenge with TLS impersonation.

### Google Places
- Cheapest flow:
  1. Text Search with field mask `places.id` (IDs only, **free, unlimited**),
     `{"textQuery": "<firma> <obec>", "languageCode": "cs", "regionCode": "CZ"}`.
  2. Place Details with mask `rating,userRatingCount,reviews,googleMapsUri,displayName`, which
     bills **Enterprise + Atmosphere** (1 000 free per month).
  Use `rating,userRatingCount` without `reviews` → Enterprise SKU (also 1 000 free, its own
  pool).
- Max 5 reviews per place. A billing account with a card is mandatory even for the free caps.
  Show the quota counter as planned.

### ddgs (web mentions)
- Quality for big employers is good. The top 10 for "Penny Market zkušenosti zaměstnanci" were:
  Atmoskop (×2), **jobinsider.cz**, **jenfirmy.cz** (IČO in the URL!), **vimvic.cz**,
  **kdepracujeme.cz** (×2), Indeed (`cmp/Penny-9/reviews`), and emimino.cz forum threads (×2).
- For small or unknown firms results are noisy: mapy.com/firmy.cz listings, Facebook groups,
  unrelated companies with similar names. Filter by IČO or name presence in the snippet before
  any fetching or AI.
- Reliability: transient "No results found" exceptions. Wrap calls in 2–3 retries with 5–10 s
  backoff, keep a small daily cap, and cache for 30 days. The `duckduckgo` backend alone failed
  every time; use `backend="auto"` (it fell through to Bing and others).

## Recommended name → IČO resolution strategy

0. **IČO in the ad** (MPSV, many JSON-LD ads): `GET /ekonomicke-subjekty/{ico}` and you are done.
1. **Local lookups first**, with no network:
   - (a) a learned cache `name_normalized → IČO` (confirmed matches);
   - (b) a small curated **brand alias table** for the ~200 biggest Czech employers (Albert →
     44012373, Lidl → 26178541, Kaufland → 25110161, Penny → 64945880, Škoda → 00177041, …),
     because brand names alone are hopeless in ARES ("Albert" = 529 hits);
   - (c) the MPSV agency list by normalized name, for agencies.
2. **Normalize the ad name**: strip quotes and punctuation. Expand `ČR`/`CR`/`CZ` → `Česká republika`
   (keep the original as a variant). Remove generic venue or prefix words (`hospoda, restaurace,
   hostinec, kavárna, bistro, pivnice, firma, společnost, provozovna, prodejna, pobočka`)
   for a second variant. Keep the legal form if present (it narrows the search).
3. **ARES query plan**, stopping at the first variant with 1–200 hits:
   - full name → expanded name → name without generic words;
   - on >1000 hits, add `pravniForma` [111,112,121,…];
   - at most ~3 requests per employer, then cache the result.
4. **Rank candidates** (top ≤50):
   - name similarity on normalized names without legal form: `rapidfuzz.fuzz.token_sort_ratio`
     or `ratio`. **Not `token_set_ratio`**: it gave 100 to every hit that merely contains the
     query (unions, foundations, persons);
   - legal-form prior: companies 111/112/121/… high; 706/707/118/145 strong penalty; 101
     (sole trader) only when the ad looks like a person or small business;
   - active (no `datumZaniku`);
   - **employee band from one RES batch call** (bigger = more likely to advertise);
   - **NACE fit** with the job category (e.g. 56xxx for kitchen or waiter jobs);
   - **sídlo municipality = job municipality** (bonus only).
5. **Decide**: if the top score ≥ threshold and the margin to #2 ≥ delta, resolve
   (`match_confidence` high). Otherwise the local AI picks from the top 3–5 using the ad text.
   Otherwise the card shows "nejisté přiřazení" with the candidates. Example: "Hospoda U Lípy"
   in Praha → "U Lípy" + s.r.o. gives 17 candidates, of which "Hostinec u Lípy, s.r.o." and
   "U LÍPY-PRAHA, spol. s r.o." are both Praha / NACE 56110, so this one is genuinely ambiguous.
   Many pubs are run by sole traders under a personal name and **cannot be resolved without an
   IČO**.
6. After resolution, run the official checks in one pass:
   - ARES detail (registrations, DIČ, NACE);
   - RES (employee band);
   - ISIR only if `stavZdrojeIr != NEEXISTUJICI`;
   - VAT from the local daily list;
   - agency licence from the local MPSV index;
   - Atmoskop URL from the local sitemap slug index.

## Deviations from PLAN.md

1. **§2.4 MPSV agencies:** the format is **JSON** (`/od/soubory/agentury-prace/agentury-prace.json`), not XML.
   Most permits have no end date, so "licence until" is often unknown.
2. **§2.4 / §4.5 Atmoskop:** there is no ★ rating, only **% satisfaction** (employer / colleagues /
   boss) and counts. The card example "★ 3.9 (124 ratings, Atmoskop)" should become e.g.
   "spokojenost 62 % (861 hodnocení, Atmoskop)". `employer_reviews.rating` needs a scale field,
   or a separate `satisfaction_pct_json`. Data comes from `__NEXT_DATA__`, not from HTML
   scraping. Profiles are matched through the sitemap slug index (no IČO on Atmoskop).
3. **§2.4 Indeed company pages:** **blocked by Cloudflare for plain HTTP.** Move them to
   "link only" in M9 (or browser-only, experimental). Re-estimate M9 slightly down.
4. **§4.5 tiers:** the insolvency flag is already in ARES (`stavZdrojeIr`), so ISIR becomes a
   detail call. The VAT register should be a **daily bulk download** (0.5 MB), not per-employer
   calls. MPSV is a daily 4 MB download. These are **two new daily refresh jobs** for M7 / the
   scheduler.
5. **§5 data model:** add `dic`, `employee_category` (RES code), `nace_main` (RES
   `czNacePrevazujici`) and `atmoskop_url` to `employers`. Add `ares_registrations_json` (or
   fold it into `employer_checks.detail_json`). Add a table or cache for `employer_aliases(name_normalized, ico, source)`.
6. **§4.5 resolution:** add the **brand alias table** and the learned cache in front of the ARES
   search. Add the RES batch call for ranking. ARES name search cannot do fuzzy or prefix
   matching, and its results are IČO-ordered, not relevance-ordered.
7. **§2.4 Google Places:** the cheapest path is the free *IDs-only* Text Search followed by
   Place Details. `reviews` bills the **Enterprise + Atmosphere** SKU (separate 1 000 free/month
   pool), not plain Enterprise.
8. **§2.4 ddgs:** works but is flaky (transient empty results). Retries and backoff are
   mandatory. It surfaced extra Czech employer-review sites not in PLAN.md: **jobinsider.cz,
   jenfirmy.cz, vimvic.cz, kdepracujeme.cz**, plus emimino.cz forums. Their robots.txt and terms
   were not checked; treat them as ordinary web mentions for now and evaluate later.
9. **Dependencies:** none beyond httpx are needed for the official checks (no zeep). `rapidfuzz`
   is needed for ranking (already implied by PLAN.md). `ddgs` 9.16.0 pulls in `primp`, a 6 MB
   native wheel.
