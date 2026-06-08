"""Zillow search query helpers (Route A: fetch via authenticated browser).

Portado de old-code/ (app.py:build_payload/json_base, web.py:get_data,
params/states.py). Monta os payloads `searchQueryState` enviados ao endpoint
interno `PUT /async-create-search-page-state` e faz parse da resposta.

Escopo atual: For Rent. O faceting adaptive (subdividir faixa de pagamento/sqft
pra furar o teto de ~20 paginas/query) vive no backend; aqui ficam os dados e os
helpers puros.
"""

import copy
import json
import os
import re
from urllib.parse import urlencode

# Endpoint interno de busca (mesmo do old-code/web.py).
SEARCH_ENDPOINT = "https://www.zillow.com/async-create-search-page-state"
# Pagina onde a aba precisa estar p/ o fetch ser same-origin.
SEARCH_PAGE_URL = "https://www.zillow.com/homes/for_rent/"

# Detalhe via POST persistedQuery no /zg-graph (header client-id: vertical-living).
# JSON puro, sem page-load/SSR. Hashes/operationNames sao auto-capturados (cache
# em SQLite) e re-capturados no expiry; os defaults abaixo sao so semente.
ZGGRAPH = "https://www.zillow.com/zg-graph"
DETAIL_ENDPOINT = ZGGRAPH  # compat
# operationName da query de detalhe (casa=property, apê=BuildingQuery). O da casa
# vem da captura; semente vazia forca capturar antes de detalhar casas.
HOUSE_OP = os.getenv("POC_HOUSE_OP", "")
APT_OP = os.getenv("POC_APT_OP", "BuildingQuery")

# Casa (/homedetails/): keyed por zpid -> data.property (resoFacts completo).
HOUSE_HASH = os.getenv(
    "POC_HOUSE_HASH",
    "751a1453e919a0631ee637b10d6a1279b38f0d14cad960a626a63bf8e8418997",
)
# Apartamento (/apartments/): BuildingQuery keyed por buildingKey -> data.building.
APT_HASH = os.getenv(
    "POC_APT_HASH",
    "22d01d244c7308f2a26252b37bda80244433f7d13d13c37946ebb65568bf728b",
)

# --- For Rent (old-code/app.py:for_rent) ---
RENT_CRITERIAS = [
    "isApartmentOrCondo",
    "isApartment",
    "isCondo",
    "isTownhouse",
    "isSingleFamily",
]
RENT_COMMONS_FALSE = [
    "isForSaleByAgent",
    "isForSaleByOwner",
    "isNewConstruction",
    "isComingSoon",
    "isAuction",
    "isForSaleForeclosure",
]
RENT_COMMONS_TRUE = ["isForRent"]
# sort_element = [primario, secundario] (secundario usado no fallback de inversao)
RENT_SORT_PRIMARY = "paymentd"
RENT_SORT_SECONDARY = "paymenta"
PAYMENT_LABEL = "monthlyPayment"

# Buckets de sqft p/ subdivisao secundaria (old-code/app.py:sqft_base).
SQFT_BASE = [0] + list(range(500, 1200, 25)) + list(range(1200, 2000, 100))

# Granularidade minima da faixa de pagamento antes de cair p/ sqft (USD/mes).
# Menor = fatia o preco mais fino antes do fallback irredutivel (1 = maximo).
try:
    PAYMENT_MIN_STEP = int(os.getenv("POC_PAYMENT_MIN_STEP", "25"))
except (TypeError, ValueError):
    PAYMENT_MIN_STEP = 25

STATES = {
     "TX": {"mapBounds": {"north": 40.31621561461024, "south": 21.375118752323395, "east": -75.66522140625, "west": -124.48846359375}, "regionId": 54},
     "AL": {"mapBounds": {"north": 35.54780116523664, "south": 29.570993984030768, "east": -80.57783122656248, "west": -92.78364177343748}, "regionId": 4},
     "AK": {"mapBounds": {"north": 73.36461860512415, "south": 47.00779398069322, "east": -82.352515625, "west": -179.999}, "regionId": 3},
     "AZ": {"mapBounds": {"north": 37.098597711679524, "south": 31.2311572341984, "east": -105.8280017265625, "west": -118.0338122734375}, "regionId": 8},
     "AR": {"mapBounds": {"north": 37.6333127574226, "south": 31.80469329901821, "east": -86.0284732265625, "west": -98.2342837734375}, "regionId": 6},
     "CA": {"mapBounds": {"north": 42.840466732353626, "south": 31.574719055396844, "east": -107.10083845312501, "west": -131.512459546875}, "regionId": 9},
     "CO": {"mapBounds": {"north": 41.72903896792921, "south": 36.216291389451136, "east": -99.4476617265625, "west": -111.6534722734375}, "regionId": 10},
     "CT": {"mapBounds": {"west": -74.28323331835938, "east": -71.23178068164063, "south": 40.83529280747761, "north": 42.164088882908985}, "regionId": 11},
     "DE": {"mapBounds": {"north": 40.5111641114161, "south": 37.75928978483818, "east": -72.33520386328125, "west": -78.43810913671875}, "regionId": 13},
     "FL": {"mapBounds": {"north": 33.840297478106635, "south": 21.296528216748758, "east": -71.59879045312502, "west": -96.01041154687502}, "regionId": 14},
     "GA": {"mapBounds": {"north": 35.643530857581645, "south": 29.67333514906193, "east": -77.0753917265625, "west": -89.2812022734375}, "regionId": 16},
     "HI": {"mapBounds": {"north": 28.376122796902504, "south": 15.235635308764136, "east": -147.9118758417293, "west": -172.3234969354793}, "regionId": 18},
     "ID": {"mapBounds": {"north": 50.35034789007535, "south": 40.41932735374271, "east": -101.93745045312498, "west": -126.34907154687498}, "regionId": 20},
     "IL": {"mapBounds": {"north": 45.0300487250345, "south": 34.12907644549177, "east": -77.060696453125, "west": -101.472317546875}, "regionId": 21},
     "IN": {"mapBounds": {"north": 42.46765926523295, "south": 37.01529467732238, "east": -80.3383717265625, "west": -92.5441822734375}, "regionId": 22},
     "IA": {"mapBounds": {"north": 44.541575277749494, "south": 39.26418797134038, "east": -87.2868927265625, "west": -99.4927032734375}, "regionId": 19},
     "KS": {"mapBounds": {"north": 41.23721748232576, "south": 35.684834402751605, "east": -92.21717272656251, "west": -104.42298327343751}, "regionId": 23},
     "KY": {"mapBounds": {"north": 39.996587687575385, "south": 35.606512039514165, "east": -79.6653347265625, "west": -91.8711452734375}, "regionId": 24},
     "LA": {"mapBounds": {"north": 33.31339524496681, "south": 28.547215911001686, "east": -85.29796472656248, "west": -97.50377527343748}, "regionId": 25},
     "ME": {"mapBounds": {"north": 49.01397627793809, "south": 41.184305752931756, "east": -56.778893953124985, "west": -81.19051504687498}, "regionId": 28},
     "MD": {"mapBounds": {"north": 39.88542287909247, "south": 37.71959305164278, "east": -74.18551386328123, "west": -80.28841913671873}, "regionId": 27},
     "MA": {"mapBounds": {"north": 43.06632168925948, "south": 41.002124609136146, "east": -68.63204886328124, "west": -74.73495413671874}, "regionId": 26},
     "MI": {"mapBounds": {"north": 48.886237597197905, "south": 41.03775376406339, "east": -74.06474295312502, "west": -98.47636404687502}, "regionId": 30},
     "MN": {"mapBounds": {"north": 50.21242643767866, "south": 42.561589477241284, "east": -81.15547995312501, "west": -105.56710104687501}, "regionId": 31},
     "MS": {"mapBounds": {"north": 37.16178698675077, "south": 27.801842067562394, "east": -77.67063795312501, "west": -102.082259046875}, "regionId": 34},
     "MO": {"mapBounds": {"north": 42.56906621402481, "south": 33.851946246154185, "east": -80.23128845312502, "west": -104.64290954687502}, "regionId": 32},
     "MT": {"mapBounds": {"north": 50.40610230991572, "south": 42.784557344518284, "east": -97.83897245312501, "west": -122.25059354687501}, "regionId": 35},
     "NE": {"mapBounds": {"north": 43.56584136835986, "south": 39.40370506858819, "east": -93.57799672656249, "west": -105.78380727343749}, "regionId": 38},
     "NV": {"mapBounds": {"north": 42.79984690682944, "south": 34.112273502132304, "east": -104.817249953125, "west": -129.22887104687499}, "regionId": 42},
     "NH": {"mapBounds": {"north": 45.98077701330857, "south": 41.98301209007785, "east": -65.4632342265625, "west": -77.6690447734375}, "regionId": 39},
     "NJ": {"mapBounds": {"north": 42.178406901527616, "south": 37.92550374004344, "east": -68.62141772656248, "west": -80.82722827343748}, "regionId": 40},
     "NM": {"mapBounds": {"north": 38.68317210989191, "south": 29.494381049627762, "east": -93.82025795312501, "west": -118.23187904687501}, "regionId": 41},
     "NY": {"mapBounds": {"north": 46.73314276131184, "south": 38.57486343036936, "east": -63.564229953125015, "west": -87.97585104687501}, "regionId": 43},
     "NC": {"mapBounds": {"north": 37.422614952458034, "south": 32.879681306408585, "east": -73.7580887265625, "west": -85.9638992734375}, "regionId": 36},
     "ND": {"mapBounds": {"north": 49.33491536531256, "south": 45.57842875852846, "east": -94.1993602265625, "west": -106.4051707734375}, "regionId": 37},
     "OH": {"mapBounds": {"north": 42.477375663646754, "south": 38.24380125417877, "east": -76.5663472265625, "west": -88.7721577734375}, "regionId": 44},
     "OK": {"mapBounds": {"north": 37.56243326099582, "south": 33.02754905218169, "east": -92.61365322656249, "west": -104.81946377343749}, "regionId": 45},
     "OR": {"mapBounds": {"north": 48.03641104732701, "south": 40.06399425655216, "east": -108.377590953125, "west": -132.789212046875}, "regionId": 46},
     "PA": {"mapBounds": {"north": 43.19284583680588, "south": 39.006041381436276, "east": -71.5017932265625, "west": -83.7076037734375}, "regionId": 47},
     "RI": {"mapBounds": {"north": 42.076835599927556, "south": 41.03691034636975, "east": -69.97218818164063, "west": -73.02364081835938}, "regionId": 50},
     "SC": {"mapBounds": {"north": 35.921854012899374, "south": 31.29438346516143, "east": -74.82370922656249, "west": -87.02951977343749}, "regionId": 51},
     "SD": {"mapBounds": {"north": 46.19589707998381, "south": 42.213159983928755, "east": -94.1442587265625, "west": -106.3500692734375}, "regionId": 52},
     "TN": {"mapBounds": {"north": 38.056178761329576, "south": 33.549954768944154, "east": -79.87569372656249, "west": -92.08150427343749}, "regionId": 53},
     "UT": {"mapBounds": {"north": 43.69817551576988, "south": 35.12722494657062, "east": -99.34121745312501, "west": -123.75283854687501}, "regionId": 55},
     "VT": {"mapBounds": {"north": 45.85243415830318, "south": 41.845733189349836, "east": -66.34856672656252, "west": -78.55437727343752}, "regionId": 58},
     "VA": {"mapBounds": {"north": 40.17484527181638, "south": 35.795717954134446, "east": -73.31801972656253, "west": -85.52383027343753}, "regionId": 56},
     "WA": {"mapBounds": {"north": 49.15269113729132, "south": 45.38272018246714, "east": -114.77937172656252, "west": -126.9851822734375}, "regionId": 59},
     "WV": {"mapBounds": {"north": 41.069783683479194, "south": 36.74632149469865, "east": -74.0787852265625, "west": -86.2845957734375}, "regionId": 61},
     "WI": {"mapBounds": {"north": 48.75085648521, "south": 40.8824862907882, "east": -77.36367995312499, "west": -101.77530104687499}, "regionId": 60},
     "WY": {"mapBounds": {"north": 45.0311449531221, "south": 40.967789440754714, "east": -101.4516617265625, "west": -113.6574722734375}, "regionId": 62},
}


def _rent_filter_state(category, sort_value, payment_range, sqft_range):
    """Monta filterState p/ For Rent (porta old-code build_payload).

    category = uma das RENT_CRITERIAS (a ativa: removida do set False).
    payment_range / sqft_range = dict {"min":.., "max":..} ou None.
    """
    filter_state = {}
    for crit in RENT_CRITERIAS + RENT_COMMONS_FALSE:
        filter_state[crit] = {"value": False}
    for crit in RENT_COMMONS_TRUE:
        filter_state[crit] = {"value": True}
    # categoria ativa fica ausente (Zillow trata ausente como incluida).
    filter_state.pop(category, None)

    filter_state["sortSelection"] = {"value": sort_value}

    if payment_range is not None:
        rng = {"min": int(payment_range["min"])}
        if payment_range.get("max") is not None:
            rng["max"] = int(payment_range["max"])
        filter_state[PAYMENT_LABEL] = rng

    if sqft_range is not None:
        rng = {"min": int(sqft_range["min"])}
        if sqft_range.get("max") is not None:
            rng["max"] = int(sqft_range["max"])
        filter_state["sqft"] = rng

    return filter_state


def build_query(state, category, page, sort_value=RENT_SORT_PRIMARY,
                payment_range=None, sqft_range=None):
    """Retorna o dict searchQueryState pronto p/ o corpo do PUT.

    state: sigla (ex 'CO'); category: RENT_CRITERIAS; page: 1-based.
    """
    if state not in STATES:
        raise ValueError(f"estado desconhecido: {state}")
    meta = STATES[state]
    return {
        "isMapVisible": False,
        "mapZoom": 6,
        "filterState": _rent_filter_state(category, sort_value, payment_range, sqft_range),
        "isListVisible": True,
        "usersSearchTerm": state,
        "mapBounds": copy.deepcopy(meta["mapBounds"]),
        "regionSelection": [{"regionType": 2, "regionId": meta["regionId"]}],
        "pagination": {"currentPage": int(page)},
    }


def build_request_body(state, category, page, sort_value=RENT_SORT_PRIMARY,
                       payment_range=None, sqft_range=None):
    """Corpo completo do PUT (searchQueryState + wants), espelha json_base."""
    return {
        "searchQueryState": build_query(
            state, category, page, sort_value, payment_range, sqft_range
        ),
        "wants": {"cat1": ["listResults"], "cat2": ["total"]},
        "requestId": 7,
        "isDebugRequest": False,
    }


def parse_results(resp_json):
    """Extrai imoveis + paginacao da resposta (porta web.py:get_data).

    Retorna {"properties": [...], "total_pages": int, "total_items": int}.
    Cada property: {url, address, beds, baths, area, zpid}.
    """
    properties = []
    total_pages = -1
    total_items = -1
    try:
        cat1 = (resp_json or {}).get("cat1", {})
        list_results = cat1.get("searchResults", {}).get("listResults", []) or []
        for item in list_results:
            if not isinstance(item, dict):
                continue
            url = item.get("detailUrl") or ""
            if not url:
                continue
            properties.append({
                "url": url,
                "zpid": item.get("zpid"),
                "address": item.get("address"),
                "beds": item.get("beds"),
                "baths": item.get("baths"),
                "area": item.get("area"),
                "price": item.get("price") or item.get("unformattedPrice"),
                "img": item.get("imgSrc") or item.get("carouselPhotos"),
                "status": item.get("statusType") or item.get("statusText"),
            })
        if properties:
            total_pages = cat1.get("searchList", {}).get("totalPages", -1)
        totals = (resp_json or {}).get("categoryTotals", {}).get("cat1", {})
        total_items = totals.get("totalResultCount", -1)
    except Exception:
        pass
    return {
        "properties": properties,
        "total_pages": total_pages,
        "total_items": total_items,
    }


def state_hash_fields(prop):
    """Campos volateis (da paginacao) que definem o 'estado' do imovel p/ o hash
    de skip. Mudou qualquer um -> re-busca detalhe."""
    return {
        "price": prop.get("price"),
        "address": prop.get("address"),
        "status": prop.get("status"),
        "img": prop.get("img"),
        "beds": prop.get("beds"),
        "baths": prop.get("baths"),
        "area": prop.get("area"),
    }


def is_apartment_url(url):
    return "/apartments/" in str(url or "")


def extract_building_key(url):
    """buildingKey = ultimo segmento de /apartments/.../<KEY>/."""
    m = re.search(r"/apartments/.*/([A-Za-z0-9]+)/?$", str(url or "").rstrip("/") + "/")
    return m.group(1) if m else None


GRAPHQL = "https://www.zillow.com/graphql/"


def _gql_get(sha256, variables, operation_name=None):
    params = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"persistedQuery": {"version": 1, "sha256Hash": sha256}},
            separators=(",", ":"),
        ),
    }
    if operation_name:
        params["operationName"] = operation_name
    return f"{GRAPHQL}?{urlencode(params)}"


def build_detail_request(url, zpid, hashes=None):
    """Roteia por tipo (evidencia da captura):
    - Casa (/homedetails/): GET /graphql, hash por zpid -> data.property.
    - Apartamento (/apartments/): POST /zg-graph BuildingQuery por buildingKey ->
      data.building (client-id: vertical-living).
    hashes = {house_op, house_hash, apt_op, apt_hash} (capturados/cacheados)."""
    h = hashes or {}
    if is_apartment_url(url):
        bkey = extract_building_key(url)
        body = {
            "operationName": h.get("apt_op") or "BuildingQuery",
            "variables": {"buildingKey": bkey, "cache": False, "latitude": None,
                          "longitude": None, "lotId": None, "update": False},
            "extensions": {"persistedQuery": {"version": 1,
                           "sha256Hash": h.get("apt_hash") or APT_HASH}},
        }
        return {"method": "POST", "endpoint": ZGGRAPH, "body": body,
                "client_id": "vertical-living", "kind": "building", "key": bkey}
    try:
        zpid_val = int(zpid)
    except (TypeError, ValueError):
        zpid_val = zpid  # zpid invalido (ex coordenada): nao crasha, GET falha gracioso
    endpoint = _gql_get(
        h.get("house_hash") or HOUSE_HASH,
        {"zpid": zpid_val, "altId": None, "deviceType": "WEB_DESKTOP"},
        operation_name=h.get("house_op") or None,
    )
    return {"method": "GET", "endpoint": endpoint, "body": None,
            "client_id": None, "kind": "property", "key": zpid}


def rent_houses_url(state):
    """Pagina de busca de CASAS do estado (onde o clique dispara o /zg-graph da
    property). O segmento de estado e dinamico/minusculo."""
    return f"https://www.zillow.com/{str(state or 'ca').lower()}/rent-houses/"


def parse_detail(resp_json):
    """Extrai property (casa) ou building (apartamento) do retorno graphql."""
    try:
        data = (resp_json or {}).get("data", {}) or {}
        return data.get("property") or data.get("building")
    except Exception:
        return None


def split_payment_range(payment_range):
    """Divide a faixa de pagamento ao meio p/ subdivisao adaptive.

    Retorna lista de sub-faixas, ou [] se ja esta no minimo de granularidade
    (chamador cai p/ sqft). payment_range pode ter max=None (ilimitado) — usa
    um teto pratico p/ poder dividir.
    """
    lo = int(payment_range.get("min", 0) or 0)
    hi = payment_range.get("max")
    # Faixa ilimitada: ancora num teto pratico de aluguel.
    if hi is None:
        hi = 20000
    hi = int(hi)
    if hi - lo <= PAYMENT_MIN_STEP:
        return []
    mid = lo + (hi - lo) // 2
    return [
        {"min": lo, "max": mid},
        {"min": mid, "max": (None if payment_range.get("max") is None else hi)},
    ]


def sqft_ranges():
    """Faixas de sqft consecutivas a partir de SQFT_BASE (ultima = ilimitada)."""
    out = []
    for i in range(len(SQFT_BASE)):
        lo = SQFT_BASE[i]
        hi = SQFT_BASE[i + 1] if i + 1 < len(SQFT_BASE) else None
        out.append({"min": lo, "max": hi})
    return out
