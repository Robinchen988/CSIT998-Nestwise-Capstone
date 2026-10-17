import json
from copy import deepcopy
import operator
import re
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.amenities import context, nearby, planning, registry, suburb_stats, zones
from app.analytics import assumptions
from app.analytics.formulas import investment_metrics, roi_certified_prefix, roi_unrounded
from app.analytics.valuation import predict_values
from app.core.config import require_llm_config
from app import i18n
from app.search.search import properties_by_ids, search_properties
from app.orchestration import answer_guard, refinement


class State(TypedDict):

    user_query: str
    intent: str
    params: dict
    properties: list[dict]
    metrics: list[dict]
    more: list[dict]
    batch_offset: int
    ranking: str
    lang: str
    search_order: str | None
    roi_pool: dict | None
    assumption_snap: dict | None
    place_lookup: dict | None
    zone_lookup: dict | None
    answer: str
    refinement_base: dict | None
    turn_notice: str | None
    history: Annotated[list, operator.add]


def _en(state) -> bool:
    return (state or {}).get("lang") == "en"


def _t(state, zh: str, en: str) -> str:
    return en if _en(state) else zh


def _kind_label(state, kind: str) -> str:
    return i18n.KINDS_EN.get(kind, kind) if _en(state) else nearby.KIND_ZH.get(kind, kind)


def _attr_label(state, attr: str) -> str:
    return (i18n.ATTRIBUTES_EN.get(attr, attr) if _en(state)
            else context.ATTRIBUTES[attr]["zh"])


def _sort_label(state, key: str) -> str:
    place = ((state or {}).get("params") or {}).get("near_place")
    if key == "near_place_distance" and place:
        return _t(state, f"离「{place['name']}」从近到远", f"Distance to {place['name']}, nearest first")
    return (i18n.sort_labels_en(_SORT_LABEL)[key] if _en(state) else _SORT_LABEL[key])


_PROPERTY_PARKING_ASK = "房源车位"
_PROPERTY_PARKING_LABELS = frozenset({"房源车位", "车位", "停车位", "车库", "car space", "car spaces",
                                      "garage", "garages", "property parking", "property car spaces"})
_PUBLIC_PARKING_RE = re.compile(
    r"公共停车(?:场|位)|停车场|(?:靠近|附近|周边|旁边)(?:有|的|公共){0,2}停车位|停车(?:控制|分区|规划|管制)"
    r"|\bpublic\s+parking\b"
    r"|\bparking\s+(?:overlay|precinct|restriction|control|zone|plan)\b"
    r"|\b(?:near|nearby|close\s+to)\s+(?:(?:a|the)\s+)?(?:public\s+)?car\s+parks?\b"
    r"|\b(?:the\s+)?garage\s+(?:cafe|restaurant|bar)\b", re.I)
_PARKING_TERM_RE = re.compile(r"车位|停车位|停车场|车库|\b(?:car\s+spaces?|car\s+parks?|parking|garages?)\b", re.I)
_PROPERTY_PARKING_RE = (
    re.compile(r"(?:房子|房源|公寓|住宅|这套|第[0-9一二三四五六七八九十]+套).{0,12}?"
               r"(?P<owned>(?:要有|要带|自带|带有|配有|需要|不需要|不要|不带|不能有|没有|有|要)"
               r"(?:至少)?(?:[0-9一二三四五六七八九十]+个?)?(?:车位|停车位|车库))"),
    re.compile(r"(?P<owned>(?:要有|要带|自带|带有|配有|需要|不需要|不要|不带|必须有|必须要)"
               r"(?:至少)?(?:[0-9一二三四五六七八九十]+个?)?(?:车位|停车位|车库))"),
    re.compile(r"\b(?P<owned>(?:with|without|has|have|needs?|wants?|requires?|must\s+have)\s+"
               r"(?:(?:a|an)\s+|at\s+least\s+\d+\s+)?"
               r"(?:car\s+spaces?|car\s+parks?|parking(?:\s+spaces?)?|garages?(?!\s+(?:cafe|restaurant|bar))))\b", re.I),
)
_DERIVED_BARE_SPACE_RE = re.compile(r"(?:garages?|car\s+spaces?|车位|停车位|车库)", re.I)
_DERIVED_AFTER_HOME_RE = re.compile(
    r"\b(?:house|home|property|apartment|flat|unit)\b(?:\s+\w+){0,4}?\s+"
    r"(?P<bare>garages?|car\s+spaces?|parking)\b(?=\s*(?:$|[,;]))", re.I)


def _property_parking_spans(text) -> list[tuple[int, int]]:
    if not isinstance(text, str):
        return []
    public = [match.span() for match in _PUBLIC_PARKING_RE.finditer(text)]
    spans = []
    for pattern in _PROPERTY_PARKING_RE:
        for match in pattern.finditer(text):
            start, end = match.span("owned")
            if any(start < stop and end > begin for begin, stop in public):
                continue
            spans.append((start, end))
    result = []
    for start, end in sorted(set(spans), key=lambda pair: (pair[0], -(pair[1] - pair[0]))):
        if not result or start >= result[-1][1]:
            result.append((start, end))
    return result


def _parking_kind(text) -> str | None:
    if not isinstance(text, str):
        return None
    if _property_parking_spans(text):
        return "property"
    public = [match.span() for match in _PUBLIC_PARKING_RE.finditer(text)]
    for match in _PARKING_TERM_RE.finditer(text):
        start, end = match.span()
        if not any(start < stop and end > begin for begin, stop in public):
            return "ambiguous"
    return None


def _without_property_parking(text: str) -> str:
    spans = _property_parking_spans(text)
    if not spans:
        return text
    parts, cursor = [], 0
    for start, end in spans:
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return re.sub(r"\s+", " ", "".join(parts)).strip(" ,;，；、")


def _without_derived_property_parking(text: str, original_query: str) -> str:
    cleaned = _without_property_parking(text)
    bare = cleaned.strip()
    if _DERIVED_BARE_SPACE_RE.fullmatch(bare):
        if bare.casefold().startswith("garage") and re.search(r"\bgarage\s+(?:cafe|restaurant|bar)\b", original_query, re.I):
            return cleaned
        return ""
    if bare.casefold() == "parking" and not _PUBLIC_PARKING_RE.search(original_query):
        return ""
    public = [match.span() for match in _PUBLIC_PARKING_RE.finditer(cleaned)]
    for match in reversed(list(_DERIVED_AFTER_HOME_RE.finditer(cleaned))):
        start, end = match.span("bare")
        if any(start < stop and end > begin for begin, stop in public):
            continue
        cleaned = cleaned[:start] + cleaned[end:]
    return re.sub(r"\s+", " ", cleaned).strip(" ,;，；、")


def _unsupported_en(ask: str) -> str:
    return "property car spaces" if ask == _PROPERTY_PARKING_ASK else i18n.UNSUPPORTED_KEY_EN.get(ask, ask)


def _parking_notice(state, kind: str | None) -> str | None:
    if kind == "property":
        return _t(state, "房源自带车位目前无法核验，未按车位筛选；结果不代表满足这项要求。",
                  "A property's own parking spaces cannot currently be verified. No parking filter was applied; the results do not establish this requirement.")
    if kind == "ambiguous":
        return _t(state, "如果你指房源自带车位，目前无法核验；原查询保留，结果不能据此判断有无车位。",
                  "If you mean a property's own parking spaces, they cannot currently be verified. Your query was kept; these results do not establish parking availability.")
    return None


_TRAVEL_TIME_ASK = "步行/驾车时间"
_TRAVEL_TIME_RE = re.compile(
    r"(步行|走路|走过去|走到|开车|驾车|坐车|车程).{0,12}(几分钟|多少分钟|多久|多长时间|\d+\s*分钟|分钟)"
    r"|(几分钟|多少分钟|多久|多长时间|\d+\s*分钟).{0,8}(步行|走路|开车|驾车|车程)"
    r"|\b(walk(?:ing)?|driv(?:e|ing))\b.{0,24}\b(minutes?|mins?|how long|time)\b"
    r"|\b(minutes?|mins?|how long)\b.{0,24}\b(walk(?:ing)?|driv(?:e|ing))\b",
    re.I)


def _asks_travel_time(query: str | None) -> bool:
    return bool(query and _TRAVEL_TIME_RE.search(query))


def _travel_time_notice(state) -> str:
    return _t(state,
              "本系统所有距离都是**直线距离**,没有路网数据,算不出步行或驾车需要几分钟。"
              "能回答的是「离 X 多远(直线)」,例如「离火车站 800 米内的两房」;房源卡片和详情里的距离也都是直线距离。",
              "All distances in this system are **straight-line**; there is no road network, so walking or driving "
              "time cannot be computed. What can be answered is \"how far from X (as the crow flies)\", e.g. "
              "\"2-bed within 800 m of a train station\"; distances on the cards and in the details are straight-line too.")


def _unsupported_list(state, asks) -> str:
    if _en(state):
        return ", ".join(_unsupported_en(a) for a in asks)
    return "、".join(asks)


_client = None
_model_name: str | None = None


def _llm():
    global _client, _model_name
    if _client is None:
        from openai import OpenAI

        api_key, model, base_url = require_llm_config()
        _client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        _model_name = model
    return _client, _model_name


def _ask(system: str, user: str, temperature: float = 0.0) -> str:
    client, model = _llm()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _ask_streaming(system: str, user: str, temperature: float = 0.0) -> str:
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
    except Exception:
        writer = None
    if writer is None:
        return _ask(system, user, temperature)

    client, model = _llm()
    stream = client.chat.completions.create(
        model=model,
        temperature=temperature,
        stream=True,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    parts: list[str] = []
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            parts.append(delta)
            writer({"token": delta})
    return "".join(parts).strip()


SEARCH_KEYS = (
    "semantic_query", "max_price", "min_price",
    "bedrooms", "bathrooms", "property_type", "suburb", "max_distance_cbd_km",
)
RANK_KEYS = ("sort_by", "min_gross_yield", "amenity_needs", "near_place",
             "abstract_needs", "unsupported_asks", "school_zone", "planning_needs", "relative_preferences")
PARAM_KEYS = SEARCH_KEYS + RANK_KEYS + ("description_query",)
PROGRAM_KEYS = ("relative_preferences",)

_INTENTS = ("new_search", "refine", "about_results", "concept", "clarify")

_INT_KEYS = ("max_price", "min_price", "bedrooms", "bathrooms")
_PROPERTY_TYPES = ("house", "apartment", "townhouse")
_SORT_FIELDS = ("gross_yield", "cap_rate", "roi", "predicted_gap", "predicted_gap_neg",
                "price_asc", "price_desc", "near_place_distance")
_ABSTRACT_KEYS = context.ATTRIBUTE_KEYS
_SCORE_OPERATORS = {
    "gte": ("≥", operator.ge), "gt": (">", operator.gt),
    "lte": ("≤", operator.le), "lt": ("<", operator.lt), "eq": ("=", operator.eq),
}

_PLANNING_NEEDS = {
    "no_heritage": (
        "没有限制改建的叠加层",
        lambda info: planning.can_redevelop(info) is True),
    "low_density_around": (
        "周边以限制加密的分区为主(法律上不会变成高楼)",
        lambda info: bool(info.get("nearby_total")) and info.get("nearby_high", 0) == 0),
    "no_risk_overlay": (
        "没有已登记的风险叠加层(政府征收、洪泛、山火、土壤污染等)",
        lambda info: "zone" in info and not planning.risks(info)),
    **{"no_" + key: (f"没有{label}",
                     (lambda fams: lambda info: "zone" in info and not planning.has_family(info, fams))(fams))
       for key, (label, fams) in planning.RISK_GROUPS.items()},
}
_SORT_FIELDS = _SORT_FIELDS + _ABSTRACT_KEYS
NEAREST_PREFIX = "nearest:"
_AMENITY_SORTS = tuple(NEAREST_PREFIX + k for k in nearby.KINDS)
_SORT_FIELDS = _SORT_FIELDS + _AMENITY_SORTS

_PARSE_SYSTEM = """你是房产搜索的参数抽取器。把用户的中文或英文问题转成结构化搜索参数。

这是**多轮对话**。用户消息里会带上前几轮的内容和上一轮的搜索条件,你要先判断
这一句是在干什么,再决定参数怎么填。

只输出一个 JSON 对象,不要 markdown 代码块,不要任何解释文字,不要前后缀。

字段(必须全部出现):
  intent         : 以下四个之一,**这是最重要的一个字段**
                   "new_search"    全新的找房需求,和上一轮无关
                                   例:上轮问 Richmond 三房,这轮说"帮我找便宜的公寓"
                   "refine"        在上一轮基础上改条件,重新找
                                   例:"只看 Richmond 的""再便宜点""改成三房"
                   "about_results" 针对**上一轮已经给出的那几套房**提问,不需要重新找
                   例:"第 3 套详细说说""为什么第 2 套估这个价""哪套最划算"
                   "concept"       问概念、名词、公式,和具体房源无关
                                   例:"什么是 cap rate""毛回报率怎么算的"
                   判断要点:说的是"再/也/还/换成/只要/不要"这类**修改**词,
                   而且没有换掉整个需求 -> refine;
                   提到"第 X 套""这几套""上面那个" -> about_results;
                   完全没有找房意图、纯问知识 -> concept;
                   其余 -> new_search。**第一轮永远是 new_search 或 concept。**

  ↓ 下面这些字段:intent 是 about_results 或 concept 时,全部填 null / [],
    因为这两种情况不会重新检索。intent 是 refine 时**不要填这些字段**,
    只在 changes 里列出本轮修改(格式见文末「多轮修改协议」),没改的条件由程序保留。
    intent 是 new_search 时,只按这一句话填,不要继承上一轮任何条件。

  semantic_query : string —— **必须用英文**。用户对房子本身的描述性要求,
                   翻译成简短的英文关键词(如 "quiet family home with good natural light")。
                   不要把价格、房间数这类硬条件写进来,它们有各自的字段。
                   用户没有描述性要求时,把用户原话的意思翻成英文。
                   为什么必须英文:库里的房源描述全是英文,嵌入模型
                   (nomic-embed-text-v1.5)也是英文模型。实测同一个意思的
                   中文 query 和英文 query,检索结果重合度是 0/5 —— 中文向量
                   落在了和英文语料不同的区域。这一项翻错,后面全白搭。
  max_price      : integer 或 null —— 价格上限,单位澳元
  min_price      : integer 或 null —— 价格下限,单位澳元
  bedrooms       : integer 或 null —— 卧室数(精确匹配)
  bathrooms      : integer 或 null —— 卫生间数(精确匹配)
  property_type  : house / apartment / townhouse 之一,或 null
  sort_by        : 以下之一,或 null —— 用户希望**按什么排序**
                   "gross_yield"    毛租金回报率高的优先(用户说"回报好""租金划算""收租香")
                   "cap_rate"       资本化率高的优先(用户明确说 cap rate / 资本化率)
                   "roi"            投资回报率高的优先(用户明确说 ROI / 投资回报)
                   "predicted_gap"  **估值高于售价**最多的优先 —— 也就是"买得便宜"。
                                    用户说"被低估的""捡漏""性价比""售价低于估值"时用。
                   "predicted_gap_neg"  **估值低于售价**最多的优先 —— 也就是"卖得贵"。
                                    用户明确说"估值低于售价""卖贵了""高于合理价"时用。
                                    ⚠️ 这两个方向**相反,不许弄混**:用户说哪个就填哪个,
                                    不要按"他大概是想找划算的"去猜 —— 猜错的话,
                                    他说出口的话和拿到的结果是反的,而且没人会发现。
                   "price_asc"      便宜的优先(用户说"最便宜""预算紧")
                   "price_desc"     贵的优先(用户说"最好的""不差钱")
                   "near_place_distance"  离 near_place(**点名的某一个地点**)最近的优先
                   "nearest:<kind>"  离某一类设施最近的优先,<kind> 必须是同时写进 amenity_needs 的那一类,
                                    例如用户说"离火车站越近越好""按到车站的距离排" →
                                    sort_by = "nearest:train_station" 且 amenity_needs 含 train_station。
                                    **只有用户明确要求按远近排序/比较("越近越好""最近的排前面""按距离排")才填**;
                                    只说"近车站""车站附近"是距离门槛(amenity_needs),sort_by 照常按其他线索填或填 null
                   或下面 abstract_needs 清单里的任一属性名,按该属性从高到低排序
                   (用户说"最安静的""通勤最方便的""最适合家庭的"时用)
                   用户提了环境要求但没有"最"这类比较级时,可以填对应属性,
                   也可以填 null 让语义相关度决定 —— 提了多个属性时,填最强调的那个。
  min_gross_yield: 小数或 null —— 毛租金回报率的下限。
                   用户说"回报率 5% 以上"就填 0.05,说"至少 4 个点"填 0.04。
                   注意是**小数不是百分数**:5% 要写 0.05,不能写 5。
  amenity_needs  : 数组,用户对**某一类设施**的距离要求。没有就填 []。
                   每项形如 {"kind": "...", "max_distance_m": 1000, "strength": "preferred"}
                   strength:默认 "preferred"(优先:近的排前面,远的仍保留);
                   用户明说"必须 / 一定要 / 不能超过 / 只要 X 米内的"才填 "required"(超出的直接剔除)。
                   kind 只能是下面这些(从注册表生成,不要用清单外的值):
@@AMENITY_KINDS@@
                   max_distance_m 是米。用户说"走路十分钟内"按 800 米算,
                   "五分钟"按 400 米,"很近"按 1000 米,没说距离就填 1500。
                   例:"有小孩,要离幼儿园和小学近"
                     -> [{"kind":"kindergarten","max_distance_m":1500},
                         {"kind":"primary_school","max_distance_m":1500}]
                   点出了具体设施类别时,"离小学近""最好离小学近一点""最好小学近一点""小学近点"
                   **都填这一项、同一个距离**("最好""一点"不改变结果);
                   只说"学校/教育配套好"、没点出哪类学校时,才用 abstract_needs 的 school_access。
  abstract_needs : 数组,用户对**居住环境**的要求。没有就填 []。
                   每项形如 {"attribute": "quiet", "min_score": 60, "operator": "gte"}。
                   operator 可为 gte(≥)、gt(>)、lte(≤)、lt(<)、eq(=);未指定则默认 gte。
                   min_score 是比较阈值,无论比较方向如何均用此字段,允许 0 和 100。
                   例:"安静低于 60 分" -> {"attribute":"quiet","min_score":60,"operator":"lt"}。
                   继续修改条件时必须保留当前 operator,不要把 < / ≤ / = 擅自改回 ≥。
                   attribute **只能**是下面清单里的。你的任务是把用户任意的说法
                   映射到清单上 —— 用户的措辞是无穷的,这份清单是有限的,
                   **映射是你的工作,不要因为用词不同就放弃**。
                   冒号后面是典型说法,不是穷举,同义的说法也要能映射上:

@@ATTRIBUTES@@

                   一句话里可以对应多个属性:
                     "带孩子住,要安静点,最好附近有小学"
                       -> family + quiet(小学点了具体类别,进 amenity_needs: primary_school 1500)
                     "上班方便、楼下能买菜的一居"
                       -> transport + convenient

                   min_score 是 0~100 的门槛,语气越强门槛越高:
                     "安静一点的" -> 60    "要很安静" -> 75    "必须非常安静" -> 85
                   没有明确语气就填 60。

                   quiet 和 lively 可以同时存在,负相关不代表逻辑互斥。不得擅自删除任何一项。
                   strength 为 required/preferred;value_source 为 explicit/inferred,
                   用于区分用户指定的分数与系统根据模糊表达推断的默认门槛。

  unsupported_asks: 数组,用户提了但**本系统没有数据**的要求。没有就填 []。
                   每项是一个简短的中文词。**这一项非常重要:宁可明说没有,
                   也不要硬塞进任何字段里。** 悄悄用一个沾边的条件顶上去,
                   用户会以为系统考虑过了 —— 那比直接说"没有"糟得多。

                   ⚠️ **这条规则管的是上面所有字段,不只是 abstract_needs。**
                   实测出过这样的错:用户说"步行 5 分钟到 CBD",模型填了
                   amenity_needs = [{"kind": "train_station", "max_distance_m": 400}]
                   —— 一个**完全不同**的条件,而且 unsupported_asks 是空的。
                   用户要的是"到 CBD 的步行时间",拿到的是"火车站 400 米内",
                   全程没有任何提示。**近似不是回答,换个条件更不是。**

                   判断标准很简单:如果你填进去的条件,**换成中文念出来和用户
                   原话不是一回事**,那就不要填,放进 unsupported_asks。

                   以下这些都要放进 unsupported_asks,**不要**映射到任何字段:
@@UNSUPPORTED@@

                   房源**自带**车位/车库目前无法核验。明确要求房子带车位或不带车位时,
                   把「房源车位」放进 unsupported_asks,不得当筛选条件、推荐理由,
                   也不要把这项要求写进 semantic_query 或 description_query。
                   附近公共停车场、规划 Parking Overlay、The Garage Cafe 等地点名称
                   与房源自带车位不同:保留其原本搜索意思,不要标成「房源车位」。
                   单独出现 parking/garage 等含义不明的词时保留原查询,不要臆断为房源车位。

                   例:"要采光好、治安好的安静三房"
                     -> abstract_needs 里只有 quiet,
                        unsupported_asks = ["采光", "治安"]
                   例:"80 万以内、4 房、Toorak、步行 5 分钟到 CBD"
                     -> max_price / bedrooms / suburb 照填,
                        amenity_needs = [](**不许**拿火车站顶替),
                        max_distance_cbd_km = null(时间不是距离,**不许**换算成公里),
                        unsupported_asks = ["步行时间"]
  max_distance_cbd_km: 数字或 null —— 到墨尔本 CBD 的**直线**距离上限,单位公里。
                   用户给了数字才填:"离 CBD 20 公里以内" -> 20,"within 10 km of the city" -> 10。
                   没给数字的"离市中心近"不填(那是 transport 等属性的事)。
                   "步行/开车 N 分钟到 CBD"是**时间**不是距离,不填,写进 unsupported_asks(步行时间)。
  school_zone    : 对象或 null —— 用户要求房子落在**某所公立学校的招生学区内**。
                   形如 {"school": "Balwyn Primary School", "level": "primary"}
                   level 是 "primary"(小学)或 "secondary"(中学),不确定填 null。
                   school 要翻成英文校名。

                   **这和 school_access 是两件事,别弄混:**
                     "附近有小学 / 离小学近"   -> amenity_needs 的 primary_school(直线距离)
                     "要在 Balwyn 小学学区内" -> school_zone(招生边界内,是个事实)
                   澳洲说的"学区"指后者。实测只有 54% 的房源"最近的小学"就是
                   "所属学区的小学" —— 拿距离回答学区,近一半会答错。

                   用户只说"学区房"却没点名学校时,填 null:我们没有学校排名数据,
                   无法判断哪个学区"好"。这时把"学校排名"放进 unsupported_asks,
                   同时可以用 school_access 表达"附近有学校"。

                   注意:**只有公立学校有招生学区**。私立、教会学校不划片,
                   用户点名的若是私立学校,填 null 并在 unsupported_asks 里说明。
  planning_needs : 数组,用户对**这块地未来会变成什么样**的要求。没有就填 []。
                   数据来自维州规划分区与叠加层(法条),只有以下三个值可选:

                     "no_heritage"        能自由改建/翻建 —— 这块地上没有历史保护、
                                          限高、须报总体规划一类的限制性叠加层。
                                          例:"想买了推倒重建"、"要能加建"、
                                              "不要历史保护建筑"
                     "low_density_around" 周边法律上不会盖起高楼。
                                          例:"以后不会被挡"、"周围别变成高楼"、
                                              "希望周边一直保持低矮"
                     "no_risk_overlay"    这块地没有**任何**已登记的风险叠加层
                                          (政府将来征收、洪泛、山火、土壤污染、机场噪声……)。
                                          例:"不要任何风险叠加层"、"风险越少越好"
                     下面这几个是**单一类别**的排除,用户点名哪一类就只填哪一类:
                     "no_airport_noise"   没有机场噪声叠加层。例:"不要机场噪音"、"避开机场噪声区"、
                                          "有机场噪音叠加层的全部排除"
                     "no_flood"           没有洪泛/内涝叠加层。例:"不要淹水的地方"、"避开洪水区"
                     "no_bushfire"        没有山火管理叠加层。例:"不要山火风险"
                     "no_acquisition"     没有政府征收叠加层。例:"别买到要被征收/拆迁的"
                     "no_contamination"   没有土壤污染(须环境审计)叠加层。例:"不要土壤污染"
                     "no_erosion"         没有侵蚀/盐渍化管理叠加层

                   **"不要 / 避开 / 排除 / 不能有" + 上面任一类风险 = 对应的 planning_needs(硬排除)**,
                   不要改写成 quiet、away_industry 这类评分偏好 —— 评分只是「最好」,
                   房子照样会出现,用户说的「不要」就落空了。
                   **只有上面这些值,不要发明新的。** 用户提的其它"未来"类要求
                   (房价会不会涨、地铁会不会修过来、学校会不会变好)一律放进
                   unsupported_asks —— 分区数据说的是"法律上能盖什么",
                   **不是**"会不会涨"。这两件事不要混。
  near_place     : 对象或 null —— 用户点名了**某一个具体地点**(不是某一类)。
                   形如 {"name": "Monash University", "kind": "university",
                         "max_distance_m": 3000}
                   name **必须翻成英文的官方名称**,因为设施库里全是英文:
                     "墨尔本大学" -> "University of Melbourne"
                     "莫纳什大学" -> "Monash University"
                     "皇家墨尔本医院" -> "Royal Melbourne Hospital"
                     "南十字星车站" -> "Southern Cross Station"
                   kind 填上面九类里最贴切的那个,能大幅提高匹配准确率。
                   max_distance_m 没提就填 null(只排序、不筛掉)。
                   **区分要点**:"离大学近"是 amenity_needs(哪所都行);
                   "离莫纳什大学近"是 near_place(指定了哪一所)。
  suburb         : string 或 null —— 地名,英文,如 "Richmond"
                   **用户只要提到任何地名(区名、郊区名、城市名),就必须原样
                   翻成英文填进这个字段,哪怕你认为那个地方不在墨尔本。**
                   例如用户说"悉尼",就填 "Sydney"。
                   有没有这个地方由数据库判断,不由你判断 —— 库里没有就返回
                   0 条,系统会如实回答"没找到",这正是我们要的。
                   你替数据库做判断、把地名藏进 semantic_query,会导致系统
                   拿一批别处的房子冒充结果,这是本系统最严重的错误。
                   只有用户完全没提地名时,这一项才填 null。

铁律:**用户没提到的字段一律填 null,绝对不要填 0、空字符串或你猜的默认值。**
max_price=null 的意思是"不筛价格";max_price=0 的意思是"要 0 元以下的房子",
会返回空结果且不报错 —— 这是最严重的错误。

单位换算:中文的"万"是 10000。"80 万" -> 800000。"一百万以内" -> max_price=1000000。
"两房""两室" -> bedrooms=2。"三房两卫" -> bedrooms=3, bathrooms=2。
"公寓" -> apartment,"别墅"/"独栋"/"独立屋"/"排屋" -> house,"联排"/"联排别墅" -> townhouse。

例(第一轮,没有历史):
用户:帮我找 80 万以下、租金回报不错的两房
{"intent": "new_search", "semantic_query": "two bedroom investment property", "max_price": 800000, "min_price": null, "bedrooms": 2, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": "gross_yield", "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [], "unsupported_asks": [], "school_zone": null, "planning_needs": [], "description_query": null}

例(接着上一轮,用户说"再便宜点,而且要离火车站近"):
{"intent": "refine", "changes": [{"action": "relative", "field": "price", "direction": "decrease", "degree": "slight", "source": "再便宜点"}, {"action": "set", "field": "amenity_needs", "value": {"kind": "train_station", "max_distance_m": 800}, "source": "离火车站近"}], "clarification": null}

例(用户:我有小孩,想找离莫纳什大学近、附近有小学的三房):
{"intent": "new_search", "semantic_query": "family home", "max_price": null, "min_price": null, "bedrooms": 3, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": "near_place_distance", "min_gross_yield": null, "amenity_needs": [{"kind": "primary_school", "max_distance_m": 1500}], "near_place": {"name": "Monash University", "kind": "university", "max_distance_m": null}, "abstract_needs": [], "unsupported_asks": [], "school_zone": null, "planning_needs": [], "description_query": null}

例(用户:安静一点的三房独栋,别靠马路):
{"intent": "new_search", "semantic_query": "three bedroom house", "max_price": null, "min_price": null, "bedrooms": 3, "bathrooms": null, "property_type": "house", "suburb": null, "max_distance_cbd_km": null, "sort_by": "quiet", "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [{"attribute": "quiet", "min_score": 60}], "unsupported_asks": [], "school_zone": null, "planning_needs": [], "description_query": null}

例(用户:找个热闹、生活方便的公寓):
{"intent": "new_search", "semantic_query": "apartment", "max_price": null, "min_price": null, "bedrooms": null, "bathrooms": null, "property_type": "apartment", "suburb": null, "max_distance_cbd_km": null, "sort_by": "lively", "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [{"attribute": "lively", "min_score": 60}, {"attribute": "convenient", "min_score": 60}], "unsupported_asks": [], "school_zone": null, "planning_needs": [], "description_query": null}

例(用户:带孩子住,要安静点,最好附近有小学,采光也要好):
{"intent": "new_search", "semantic_query": "family home", "max_price": null, "min_price": null, "bedrooms": null, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": "family", "min_gross_yield": null, "amenity_needs": [{"kind": "primary_school", "max_distance_m": 1500}], "near_place": null, "abstract_needs": [{"attribute": "family", "min_score": 60}, {"attribute": "quiet", "min_score": 60}], "unsupported_asks": ["采光"], "school_zone": null, "planning_needs": [], "description_query": null}

例(用户:父母要住,看病方便点,房子得宽敞,治安也重要):
{"intent": "new_search", "semantic_query": "spacious home", "max_price": null, "min_price": null, "bedrooms": null, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": "medical", "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [{"attribute": "medical", "min_score": 60}, {"attribute": "spacious", "min_score": 60}], "unsupported_asks": ["治安"], "school_zone": null, "planning_needs": [], "description_query": null}

例(用户:要在 Balwyn 小学学区内的三房):
{"intent": "new_search", "semantic_query": "three bedroom home", "max_price": null, "min_price": null, "bedrooms": 3, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": null, "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [], "unsupported_asks": [], "school_zone": {"school": "Balwyn Primary School", "level": "primary"}, "planning_needs": [], "description_query": null}

例(用户:想买个老房子推倒重建,周围以后别盖起高楼):
{"intent": "new_search", "semantic_query": "older house for redevelopment", "max_price": null, "min_price": null, "bedrooms": null, "bathrooms": null, "property_type": "house", "suburb": null, "max_distance_cbd_km": null, "sort_by": null, "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [], "unsupported_asks": [], "school_zone": null, "planning_needs": ["no_heritage", "low_density_around"], "description_query": "older house for redevelopment"}

例(用户:找个安静的三房,别买到将来要拆迁或者会淹水的,顺便说下会不会升值):
{"intent": "new_search", "semantic_query": "quiet three bedroom home", "max_price": null, "min_price": null, "bedrooms": 3, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": "quiet", "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [{"attribute": "quiet", "min_score": 60}], "unsupported_asks": ["升值潜力"], "school_zone": null, "planning_needs": ["no_risk_overlay"], "description_query": null}
(注意:"拆迁"和"淹水"都是 no_risk_overlay 能覆盖的**登记事实**;
 "会不会升值"是预测,分区数据答不了,进 unsupported_asks)

例(用户:第 3 套为什么估值这么高):
{"intent": "about_results", "semantic_query": null, "max_price": null, "min_price": null, "bedrooms": null, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": null, "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [], "unsupported_asks": [], "school_zone": null, "planning_needs": [], "description_query": null}

例(用户:cap rate 是什么意思):
{"intent": "concept", "semantic_query": null, "max_price": null, "min_price": null, "bedrooms": null, "bathrooms": null, "property_type": null, "suburb": null, "max_distance_cbd_km": null, "sort_by": null, "min_gross_yield": null, "amenity_needs": [], "near_place": null, "abstract_needs": [], "unsupported_asks": [], "school_zone": null, "planning_needs": [], "description_query": null}
"""


_PARSE_SYSTEM = (
    _PARSE_SYSTEM
    .replace("@@ATTRIBUTES@@", registry.prompt_block())
    .replace("@@UNSUPPORTED@@", registry.unsupported_block())
    .replace("@@AMENITY_KINDS@@", registry.amenity_kinds_block())
) + refinement.PATCH_PROMPT
assert "@@" not in _PARSE_SYSTEM, "提示词里还有没被替换掉的占位符"

_FIRST_TURN_RETRY = ("\n\n注意:这是对话的第一句,**还没有任何搜索结果**,所以 intent 不可能是 about_results。"
                     "「哪套…最高/最便宜」这类问题是要你去找房:intent 填 new_search,"
                     "并把这句话里的条件(价格、房数、房型、区域、排序等)照常抽进对应字段;"
                     "只是在问概念或公式时才填 concept。")


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    fenced = re.search(r"`{3}(?:json)?\s*(.+?)\s*`{3}", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _typo_source(suburb: str, user_query: str) -> str | None:
    if not user_query:
        return None
    if re.search(r"(?<![A-Za-z])" + re.escape(suburb) + r"(?![A-Za-z])", user_query, re.I):
        return None
    import difflib
    words = re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", user_query)
    hit = difflib.get_close_matches(suburb.lower(), [w.lower() for w in words], n=1, cutoff=0.6)
    if not hit:
        return None
    for w in words:
        if w.lower() == hit[0]:
            return w
    return hit[0]


_GAP_UP = re.compile(r"(售价|价格|报价|指导价)?低于(模型)?估值|估值高于(售价|价格|报价|指导价)|被低估|低估(?!值)|捡漏"
                     r"|under-?valued|below (its |the )?(model )?(estimate|valuation)|priced below", re.I)
_GAP_DOWN = re.compile(r"(售价|价格|报价|指导价)?高于(模型)?估值|估值低于(售价|价格|报价|指导价)|被高估|高估(?!值)|卖贵"
                       r"|over-?valued|over-?priced|above (its |the )?(model )?(estimate|valuation)|priced above", re.I)


def _valuation_direction(user_query: str, sort_by):
    if sort_by not in (None, "predicted_gap", "predicted_gap_neg"):
        return sort_by
    up, down = bool(_GAP_UP.search(user_query or "")), bool(_GAP_DOWN.search(user_query or ""))
    if up and not down:
        return "predicted_gap"
    if down and not up:
        return "predicted_gap_neg"
    return sort_by


def _sanitize(raw: dict, user_query: str, *, preserve_property_parking=False,
              allow_property_parking=True) -> dict:
    params: dict = {key: None for key in PARAM_KEYS}
    if isinstance(raw.get("description_query"), str):
        params["description_query"] = raw["description_query"].strip()[:500] or None

    for key in _INT_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            params[key] = int(value)

    cbd = raw.get("max_distance_cbd_km")
    if isinstance(cbd, (int, float)) and not isinstance(cbd, bool) and 0 < cbd <= 200:
        params["max_distance_cbd_km"] = round(float(cbd), 1)

    ptype = raw.get("property_type")
    if isinstance(ptype, str) and ptype.strip().lower() in _PROPERTY_TYPES:
        params["property_type"] = ptype.strip().lower()

    suburb = raw.get("suburb")
    if isinstance(suburb, str) and suburb.strip():
        params["suburb"] = suburb.strip()
        params["suburb_typed_as"] = _typo_source(params["suburb"], user_query)

    sq = raw.get("semantic_query")
    params["semantic_query"] = sq.strip() if isinstance(sq, str) and sq.strip() else user_query

    if params["min_price"] and params["max_price"] and params["min_price"] > params["max_price"]:
        params["min_price"] = None

    sort_by = raw.get("sort_by")
    if isinstance(sort_by, str) and sort_by.strip() in _SORT_FIELDS:
        params["sort_by"] = sort_by.strip()
    params["sort_by"] = _valuation_direction(user_query, params["sort_by"])

    mgy = raw.get("min_gross_yield")
    if not isinstance(mgy, bool) and isinstance(mgy, (int, float)) and mgy > 0:
        params["min_gross_yield"] = float(mgy) / 100 if mgy > 1 else float(mgy)

    needs = []
    for item in raw.get("amenity_needs") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if not isinstance(kind, str) or kind.strip() not in nearby.KINDS:
            continue
        distance = item.get("max_distance_m")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or distance <= 0:
            distance = 1500
        strength = "required" if item.get("strength") == "required" else "preferred"
        needs.append({"kind": kind.strip(), "max_distance_m": int(distance), "strength": strength})
    params["amenity_needs"] = needs or None

    wanted = []
    for item in raw.get("abstract_needs") or []:
        if not isinstance(item, dict):
            continue
        attr = item.get("attribute")
        if not isinstance(attr, str) or attr.strip() not in _ABSTRACT_KEYS:
            continue
        floor = item.get("min_score")
        if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 <= floor <= 100:
            floor = 60
        need = {"attribute": attr.strip(), "min_score": int(floor)}
        if "operator" in item:
            op = item["operator"]
            need["operator"] = op if isinstance(op, str) and op in _SCORE_OPERATORS else "gte"
        for key, allowed in (("strength", ("required", "preferred")),
                             ("value_source", ("explicit", "inferred"))):
            if item.get(key) in allowed:
                need[key] = item[key]
        wanted.append(need)
    params["abstract_needs"] = wanted or None
    params["relative_preferences"] = refinement.valid_goals(raw.get("relative_preferences")) or None

    asks = [a.strip() for a in (raw.get("unsupported_asks") or [])
            if isinstance(a, str) and a.strip()]
    parking_kind = _parking_kind(user_query)
    had_property_parking = any(ask.casefold() in _PROPERTY_PARKING_LABELS for ask in asks)
    asks = [ask for ask in asks if ask.casefold() not in _PROPERTY_PARKING_LABELS]
    if allow_property_parking and (parking_kind == "property" or
                                   (preserve_property_parking and had_property_parking)):
        asks.insert(0, _PROPERTY_PARKING_ASK)
    if allow_property_parking and parking_kind == "property":
        params["semantic_query"] = _without_derived_property_parking(params["semantic_query"], user_query) or "property"
        if params["description_query"]:
            params["description_query"] = _without_derived_property_parking(params["description_query"], user_query) or None
    if _asks_travel_time(user_query):
        asks = [a for a in asks if not re.search(r"步行|走路|驾车|开车|walk|driv", a, re.I)]
        asks.insert(0, _TRAVEL_TIME_ASK)
    params["unsupported_asks"] = asks[:6] or None

    wanted_planning = [p for p in (raw.get("planning_needs") or [])
                       if isinstance(p, str) and p in _PLANNING_NEEDS]
    params["planning_needs"] = list(dict.fromkeys(wanted_planning)) or None

    zone = raw.get("school_zone")
    if isinstance(zone, dict) and isinstance(zone.get("school"), str) and zone["school"].strip():
        level = zone.get("level")
        params["school_zone"] = {
            "school": zone["school"].strip(),
            "level": level if level in zones.LEVELS else None,
        }

    place = raw.get("near_place")
    if isinstance(place, dict) and isinstance(place.get("name"), str) and place["name"].strip():
        kind = place.get("kind")
        distance = place.get("max_distance_m")
        params["near_place"] = {
            "name": place["name"].strip(),
            "kind": kind.strip() if isinstance(kind, str) and kind.strip() in nearby.KINDS else None,
            "max_distance_m": int(distance) if (not isinstance(distance, bool)
                                                and isinstance(distance, (int, float))
                                                and distance > 0) else None,
        }

    if params["sort_by"] == "near_place_distance" and not params["near_place"]:
        params["sort_by"] = None
    if (params["sort_by"] or "").startswith(NEAREST_PREFIX):
        kind = params["sort_by"][len(NEAREST_PREFIX):]
        if not any(n.get("kind") == kind for n in params.get("amenity_needs") or []):
            params["sort_by"] = None

    return params


def prepare_refinement(raw: dict, previous: dict, removed_attributes=(), user_query=None,
                       preserve_property_parking=None, allow_property_parking=True) -> dict:
    if preserve_property_parking is None:
        preserve_property_parking = _PROPERTY_PARKING_ASK in (previous.get("unsupported_asks") or [])
    clean = _sanitize(dict(raw), user_query or raw.get("semantic_query") or previous.get("semantic_query") or "property",
                      preserve_property_parking=preserve_property_parking,
                      allow_property_parking=allow_property_parking)
    old_attrs = {n["attribute"] for n in previous.get("abstract_needs") or []}
    active = {n["attribute"] for n in clean.get("abstract_needs") or []}
    removed = (old_attrs | (set(removed_attributes) & set(_ABSTRACT_KEYS))) - active
    if clean.get("sort_by") in removed:
        clean["sort_by"] = None
    clean["relative_preferences"] = [g for g in clean.get("relative_preferences") or []
                                     if g["field"] not in removed] or None
    old_needs = {n["attribute"]: n for n in previous.get("abstract_needs") or []}
    changed_scores = {n["attribute"] for n in clean.get("abstract_needs") or []
                      if n["attribute"] in old_needs and n != old_needs[n["attribute"]]}
    clean["relative_preferences"] = [g for g in clean.get("relative_preferences") or []
                                     if g["field"] not in changed_scores] or None
    if clean.get("sort_by") != previous.get("sort_by") and clean.get("sort_by"):
        clean["relative_preferences"] = None

    filter_keys = [k for k in PARAM_KEYS if k not in ("semantic_query", "sort_by", "unsupported_asks")]
    changed = bool(removed) or any(previous.get(k) != clean.get(k) for k in filter_keys)
    if changed:
        terms = [clean.get("property_type") or "property"]
        if clean.get("description_query"):
            terms.append(clean["description_query"])
        if clean.get("bedrooms"):
            terms.append(f"{clean['bedrooms']} bedrooms")
        if clean.get("bathrooms"):
            terms.append(f"{clean['bathrooms']} bathrooms")
        if clean.get("suburb"):
            terms.append("in " + clean["suburb"])
        terms.extend(i18n.ATTRIBUTES_EN[n["attribute"]] for n in clean.get("abstract_needs") or [])
        terms.extend(i18n.ATTRIBUTES_EN[g["field"]] for g in clean.get("relative_preferences") or []
                     if g["field"] in _ABSTRACT_KEYS)
        terms.extend("near " + i18n.KINDS_EN.get(n["kind"], n["kind"].replace("_", " "))
                     for n in clean.get("amenity_needs") or [])
        if clean.get("near_place"):
            terms.append("near " + clean["near_place"]["name"])
        if clean.get("school_zone"):
            terms.append("school zone " + clean["school_zone"]["school"])
        terms.extend(i18n.PLANNING_NEEDS_EN[k] for k in clean.get("planning_needs") or [])
        clean["semantic_query"] = "; ".join(terms)
    return clean


HISTORY_TURNS = 4


def _context_message(state: State) -> str:
    parts = []
    history = state.get("history") or []
    if history:
        recent = history[-HISTORY_TURNS * 2:]
        lines = "\n".join(f"  {h['role']}: {h['text']}" for h in recent)
        parts.append(f"前几轮对话:\n{lines}")

    last_params = state.get("params") or {}
    if last_params:
        shown = {k: v for k, v in last_params.items() if v not in (None, [], {})}
        parts.append("当前生效的搜索条件(refine 时只在此基础上改;条件卡已删除的要求不能从旧对话恢复,除非用户本轮明确重新提出):\n  "
                     + json.dumps(shown, ensure_ascii=False))

    metrics = state.get("metrics") or []
    if metrics:
        brief = [f"第 {i} 套 {m.get('suburb')} ${m.get('price')} "
                 f"{m.get('bedrooms')}房{m.get('bathrooms')}卫; "
                 f"系统相对评分 {json.dumps(m.get('context_scores') or {}, ensure_ascii=False)}"
                 for i, m in enumerate(metrics, int(state.get('batch_offset') or 0) + 1)]
        parts.append("上一轮给出的房源(about_results 指的就是这几套):\n  "
                     + "; ".join(brief))

    parts.append(f"用户这一句:{state['user_query']}")
    parts.append("界面语言:English" if _en(state) else "界面语言:中文")
    return "\n\n".join(parts)


def parse_intent(state: State) -> dict:
    user_query = state["user_query"]
    try:
        raw = _extract_json(_ask(_PARSE_SYSTEM, _context_message(state)))
    except Exception:
        raw = None

    history = [{"role": "用户", "text": user_query}]
    def clarify(message=None):
        return {"intent": "clarify", "params": state.get("params") or {},
                "refinement_base": None, "history": history,
                "turn_notice": message or _t(state,
                    "我还不能确定这次要修改哪项条件,已保留原条件和房源。请说明要增加、取消或调整哪项要求;相对调整需要上一轮有对应的数据。",
                    "I kept your filters and results. Please specify which requirement to add, remove or adjust; a relative adjustment needs matching data from the previous results.")}

    if raw is None:
        if _parking_kind(user_query) == "property":
            return {"intent": "clarify", "params": state.get("params") or {},
                    "refinement_base": None, "history": history,
                    "turn_notice": _parking_notice(state, "property")}
        if state.get("params") or state.get("metrics"):
            return clarify()
        return {"intent": "new_search",
                "params": {**{k: None for k in PARAM_KEYS}, "semantic_query": user_query},
                "refinement_base": None, "turn_notice": None, "history": history}

    intent = raw.get("intent")
    if not isinstance(intent, str) or intent.strip() not in _INTENTS:
        if state.get("params") or state.get("metrics"):
            return clarify()
        intent = "new_search"
    else:
        intent = intent.strip()

    if intent == "about_results" and not state.get("metrics"):
        try:
            retry = _extract_json(_ask(_PARSE_SYSTEM, _context_message(state) + _FIRST_TURN_RETRY))
        except Exception:
            retry = None
        retry_intent = retry.get("intent").strip() if retry and isinstance(retry.get("intent"), str) else None
        if retry_intent not in ("new_search", "refine", "concept", "clarify"):
            return clarify(_t(state,
                "这句话我没能确定要按哪些条件找房。为避免按错误的条件给出房源,这一轮没有检索。"
                "请换个说法(例如「60 万以下、年租金最高的房子」),或用「+ 添加条件」直接设置。",
                "I could not tell which conditions to search by, so nothing was searched this turn rather than "
                "risk showing homes that ignore what you asked. Please rephrase (e.g. \"homes under $600k with the "
                "highest annual rent\") or set conditions with \"+ Add a filter\"."))
        raw, intent = retry, retry_intent
    fresh = intent == "refine" and not (state.get("params") or state.get("metrics"))

    if intent == "clarify":
        message = raw.get("clarification")
        return clarify(message[:500] if isinstance(message, str) and message.strip() else None)

    if intent == "new_search" and state.get("params"):
        reset_source = raw.get("reset_source")
        if not isinstance(reset_source, str) or not reset_source.strip() or reset_source.casefold() not in user_query.casefold():
            return clarify()

    if intent in ("about_results", "concept"):
        notice = _travel_time_notice(state) if _asks_travel_time(user_query) else None
        return {"intent": intent, "params": state.get("params") or {},
                "turn_notice": notice, "refinement_base": None, "history": history}

    if intent == "refine":
        try:
            previous = state.get("params") or {}
            changes = raw.get("changes")
            if fresh and isinstance(changes, list):
                changes = [{"action": "set", "field": c["field"], "source": c.get("source"),
                            "value": {"min_score": 60, "operator": "gte", "strength": "preferred", "value_source": "inferred"}}
                           if isinstance(c, dict) and c.get("action") == "relative" and c.get("field") in _ABSTRACT_KEYS
                           and c.get("direction") == "increase" else c for c in changes]
            removed_property_parking = any(
                isinstance(change, dict) and change.get("action") == "remove"
                and change.get("field") == "unsupported_asks"
                and isinstance(change.get("value"), str)
                and change["value"].casefold() in _PROPERTY_PARKING_LABELS
                for change in (changes if isinstance(changes, list) else []))
            preserve_property_parking = _PROPERTY_PARKING_ASK in (previous.get("unsupported_asks") or [])
            def sanitize_refinement(params, query):
                return _sanitize(params, query, preserve_property_parking=preserve_property_parking,
                                 allow_property_parking=not removed_property_parking)
            changed = refinement.apply_changes(previous, changes,
                user_query, state.get("metrics") or [], sanitize_refinement, _SORT_FIELDS)
            clean = prepare_refinement(changed, previous, user_query=user_query,
                preserve_property_parking=preserve_property_parking,
                allow_property_parking=not removed_property_parking)
            clean["relative_preferences"] = changed.get("relative_preferences")
        except (refinement.ClarifyChange, TypeError, ValueError, KeyError):
            return clarify()
        if fresh:
            return {"intent": "new_search", "params": clean, "refinement_base": None,
                    "turn_notice": None, "history": history}
        return {"intent": intent, "params": clean, "refinement_base": refinement.snapshot(state),
                "turn_notice": None, "history": history}

    return {"intent": intent,
            "params": _sanitize({**raw, "relative_preferences": None}, user_query),
            "refinement_base": None, "turn_notice": None, "history": history}


def detail_metrics(ids: list[int], lang: str = "zh") -> list[dict]:
    state = {"properties": properties_by_ids(ids), "params": {}, "lang": lang, "user_query": ""}
    if not state["properties"]:
        return []
    for node in (analyze, enrich, present):
        state.update(node(state))
    return state["metrics"]


RESULT_LIMIT = 5

CANDIDATE_LIMIT = 5000


_SQL_ORDER = {"price_asc": "price_asc", "price_desc": "price_desc",
              "gross_yield": "gross_yield", "cap_rate": "gross_yield", "roi": "roi"}


def search(state: State) -> dict:
    params = {key: state["params"].get(key) for key in SEARCH_KEYS}
    if not params.get("semantic_query"):
        params["semantic_query"] = state["user_query"]

    needs_pool = any(state["params"].get(k) for k in RANK_KEYS
                     if k != "unsupported_asks")
    limit = CANDIDATE_LIMIT if needs_pool else RESULT_LIMIT
    if state.get("roi_limit"):
        limit = max(limit, int(state["roi_limit"]))
    order_by = _SQL_ORDER.get(state["params"].get("sort_by"))
    extra, roi_pool, snap = {}, None, state.get("assumption_snap")
    if state.get("roi_limit") and snap:
        pass
    else:
        display = deepcopy(assumptions.snapshot())
        snap = {"opex_rate": display["opex_rate"],
                "other_costs": display["other_acquisition_costs"],
                "display": display}
    if order_by == "roi":
        extra = {"opex_rate": snap["opex_rate"], "other_costs": snap["other_costs"]}
    properties = search_properties(**params, limit=limit + (1 if order_by == "roi" else 0), order_by=order_by, **extra)
    if order_by == "roi" and len(properties) > limit:
        properties = properties[:limit]
        last = properties[-1]
        roi_pool = {"cutoff": roi_unrounded(last.get("price"), last.get("annual_rent"), extra["opex_rate"], extra["other_costs"]),
                    "opex_rate": extra["opex_rate"], "other_costs": extra["other_costs"], "limit": limit}
    return {"properties": properties, "search_order": order_by, "roi_pool": roi_pool, "assumption_snap": snap}


_SUBURB_CACHE: dict = {}


def _dataset_suburbs() -> list[str]:
    if "names" not in _SUBURB_CACHE:
        try:
            from app.core.db import get_connection
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT suburb FROM properties WHERE suburb IS NOT NULL")
                _SUBURB_CACHE["names"] = [row[0] for row in cur.fetchall()]
        except Exception:
            return []
    return _SUBURB_CACHE["names"]


def _rent_sources(ids) -> dict:
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return {}
    from app.core.db import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, rent_source FROM properties WHERE id = ANY(%s)", (ids,))
        return dict(cur.fetchall())


def analyze(state: State) -> dict:
    snap = state.get("assumption_snap")
    if snap:
        opex_rate, other_costs, assumption_snapshot = snap["opex_rate"], snap["other_costs"], snap["display"]
    else:
        opex_rate = assumptions.opex_rate()
        other_costs = assumptions.other_acquisition_costs()
        assumption_snapshot = assumptions.snapshot()

    valuations = predict_values([{
        "suburb": p.get("suburb"),
        "address": p.get("address"),
        "property_type": p.get("property_type"),
        "bedrooms": p.get("bedrooms"),
        "bathrooms": p.get("bathrooms"),
        "car_spaces": p.get("car_spaces"),
        "land_size": p.get("land_size"),
        "building_area": p.get("building_area"),
        "distance_cbd": p.get("distance_cbd"),
        "latitude": p.get("latitude"),
        "longitude": p.get("longitude"),
        "price": p.get("price"),
    } for p in state["properties"]])

    rent_sources = _rent_sources([p.get("id") for p in state["properties"]])

    metrics = []
    for prop, valuation in zip(state["properties"], valuations):
        price = prop.get("price")
        annual_rent = prop.get("annual_rent")

        inv = investment_metrics(price, annual_rent, opex_rate, other_costs)

        predicted = valuation["predicted_price"]
        lo, hi = valuation.get("interval_low"), valuation.get("interval_high")
        position = None
        if price and lo is not None and hi is not None:
            position = "below" if price < lo else "above" if price > hi else "within"

        metrics.append({
            "id": prop.get("id"),
            "suburb": prop.get("suburb"),
            "address": prop.get("address"),
            "property_type": prop.get("property_type"),
            "price": price,
            "bedrooms": prop.get("bedrooms"),
            "bathrooms": prop.get("bathrooms"),
            "car_spaces": prop.get("car_spaces"),
            "distance_cbd": nearby.distance_to_cbd_km(prop.get("latitude"), prop.get("longitude"),
                                                      prop.get("distance_cbd")),
            "annual_rent": annual_rent,
            "rent_source": rent_sources.get(prop.get("id")),
            "latitude": prop.get("latitude"),
            "longitude": prop.get("longitude"),
            "land_size": prop.get("land_size"),
            "building_area": prop.get("building_area"),
            "gross_yield": inv["gross_yield"],
            "operating_expenses": inv["operating_expenses"],
            "noi": inv["noi"],
            "cap_rate": inv["cap_rate"],
            "roi": inv["roi"],
            "stamp_duty": inv["stamp_duty"],
            "total_cost": inv["total_cost"],
            "assumptions": assumption_snapshot,
            "predicted_price": predicted,
            "valuation_is_stub": valuation["is_stub"],
            "valuation_cross_fitted": valuation.get("cross_fitted"),
            "valuation_error_pct": valuation.get("typical_error_pct"),
            "valuation_range": [valuation.get("range_low"), valuation.get("range_high")],
            "valuation_interval": [lo, hi],
            "valuation_interval_level": valuation.get("interval_level"),
            "valuation_interval_coverage": valuation.get("interval_coverage"),
            "valuation_position": position,
            "predicted_gap": (predicted - price) / price if price and predicted else None,
        })
        metrics[-1]["area_suspect"] = area_suspect(metrics[-1])
    return {"metrics": metrics}


DEFAULT_AMENITY_KINDS = ("train_station", "primary_school", "hospital", "bank")


def enrich(state: State) -> dict:
    params = state.get("params") or {}
    needs = params.get("amenity_needs") or []
    want_context_kinds = (bool(params.get("abstract_needs")) or bool(params.get("relative_preferences"))
                          or params.get("sort_by") in _ABSTRACT_KEYS)
    kinds = tuple(dict.fromkeys(
        [n["kind"] for n in needs]
        + list(DEFAULT_AMENITY_KINDS)
        + (["secondary_school", "kindergarten"] if want_context_kinds else [])
    ))

    place = params.get("near_place")
    place_points, place_error = [], None
    if place:
        place_points = nearby.find_place(place["name"], place.get("kind"))
        if not place_points:
            place_error = f"设施库里找不到「{place['name']}」"

    want_context = (bool(params.get("abstract_needs")) or bool(params.get("relative_preferences"))
                    or params.get("sort_by") in _ABSTRACT_KEYS)

    rows = state["metrics"]
    evidences = context.collect_evidence_batch(rows) if want_context else [{}] * len(rows)

    lats = [m.get("latitude") for m in rows]
    lons = [m.get("longitude") for m in rows]
    filter_kinds = tuple(dict.fromkeys(n["kind"] for n in needs))
    amenity_rows = (nearby.nearest_by_kind_batch(lats, lons, filter_kinds)
                    if filter_kinds else [{} for _ in rows])

    zone_req = params.get("school_zone")
    zone_hits, zone_error = [], None
    in_zone = [True] * len(rows)
    if zone_req:
        zone_hits = zones.find_zone(zone_req["school"], zone_req.get("level"))
        if zone_hits:
            in_zone = zones.in_zone_batch(lats, lons, zone_hits)
        else:
            zone_error = f"学区数据里找不到「{zone_req['school']}」(只覆盖公立学校)"

    planning_rows = (planning.for_batch(lats, lons)
                     if params.get("planning_needs") else [{}] * len(rows))

    metrics = []
    for m, ev, am in zip(rows, evidences, amenity_rows):
        lat, lon = m.get("latitude"), m.get("longitude")
        entry = dict(m)
        entry["amenities"] = am
        entry["near_place"] = (
            nearby.distance_to_place(lat, lon, place_points) if place_points else None
        )
        if want_context and ev:
            entry["context_evidence"] = ev
            entry["context_scores"] = context.scores(
                ev, entry.get("property_type"), entry.get("bedrooms"))
            entry["context_parts"] = context.part_strength(
                ev, entry.get("property_type"), entry.get("bedrooms"))
            entry["context_ranks"] = {
                a: r for a, v in entry["context_scores"].items()
                if (r := context.score_rank(a, v)) is not None}
        metrics.append(entry)

    for entry, inside, plan in zip(metrics, in_zone, planning_rows):
        entry["in_requested_zone"] = bool(inside)
        if plan:
            entry["planning"] = plan

    return {"metrics": metrics,
            "place_lookup": {"query": place, "found": len(place_points),
                             "error": place_error} if place else None,
            "zone_lookup": {"query": zone_req, "found": len(zone_hits),
                            "error": zone_error} if zone_req else None}


_SORT_LABEL = {
    "gross_yield": "毛租金回报率从高到低",
    "cap_rate": "资本化率从高到低",
    "roi": "投资回报率从高到低",
    "predicted_gap": "估值高于售价的幅度从大到小",
    "predicted_gap_neg": "估值低于售价的幅度从大到小",
    "price_asc": "价格从低到高",
    "price_desc": "价格从高到低",
    "near_place_distance": "离指定地点从近到远",
    **registry.sort_labels(),
    **{NEAREST_PREFIX + k: f"距{v}从近到远" for k, v in nearby.KIND_ZH.items()},
}


def _score_shift(state, goals, needs, previous_rows, shown):
    fields = [g["field"] for g in goals if g["field"] in _ABSTRACT_KEYS]
    held = {n["attribute"] for n in needs or []}
    fields += [refinement.OPPOSITE[f] for f in fields if refinement.OPPOSITE.get(f) in held]
    parts = []
    for field in dict.fromkeys(fields):
        before = refinement.baseline_for(previous_rows, field)
        after = refinement.baseline_for(shown, field)
        if before is None or after is None:
            continue
        diff = round(after - before)
        parts.append(f"「{_attr_label(state, field)}」" + _t(state, "中位 ", " median ")
                     + f"{before:.0f}→{after:.0f}({diff:+d})")
    if not parts:
        return None
    return _t(state, "评分变化(系统相对评分):", "Score change (system relative scores): ") + _t(state, ",", ", ").join(parts)


def _tradeoff_hint(state, params, goals):
    moving = {refinement.OPPOSITE.get(g["field"]) for g in goals}
    for need in params.get("abstract_needs") or []:
        if need["attribute"] not in moving:
            continue
        label, floor = _attr_label(state, need["attribute"]), need["min_score"]
        if need.get("value_source") == "explicit" or need.get("strength") == "required":
            return _t(state,
                f"「{label}」≥{floor} 是你明确要求的,系统不会自动降低。如果愿意放宽,请在条件卡里修改或直接告诉我。",
                f"“{label}” ≥{floor} is your explicit requirement, so it will not be lowered automatically. "
                f"Edit it on the filter card or tell me if you want to relax it.")
        if floor > refinement.AUTO_FLOOR:
            return None
        return _t(state,
            f"再往下调需要把「{label}」门槛降到 {refinement.AUTO_FLOOR} 以下,那样就称不上{label}了,需要你确认。"
            f"也可以在条件卡里直接修改分数。",
            f"Going further would take the “{label}” threshold below {refinement.AUTO_FLOOR}, which no longer "
            f"counts as {label.lower()}. Please confirm, or edit the score on the filter card.")
    return None


def _unmatched_refinement(state, notes, relative=False, hint=None):
    message = _t(state,
        "本次检索范围内,没有找到满足现有条件且能适度向该方向调整的房源。" if relative else
        "本次检索范围内,没有找到同时满足这些条件的房源。",
        "No candidates in this search can make a moderate adjustment while meeting your filters." if relative else
        "No candidates in this search meet all these requirements together.")
    if hint:
        message += hint
    base = state.get("refinement_base")
    if base and base.get("metrics"):
        message += _t(state, "已保留上一轮条件和房源。请明确希望放宽哪项要求,或保留当前结果。",
                      " Your previous filters and results are unchanged. Specify a requirement to relax, or keep the current results.")
        return {**base, "turn_notice": message, "refinement_base": None}
    return {"metrics": [], "more": [], "batch_offset": 0,
            "ranking": ";".join([message] + notes), "turn_notice": None, "refinement_base": None}


def _prefer_amenities(state: State, metrics: list[dict], notes: list[str], abstract: bool = True) -> list[dict]:
    params = state.get("params") or {}
    tests = []
    for n in params.get("amenity_needs") or []:
        if n.get("strength") == "required":
            continue
        label = _kind_label(state, n["kind"])
        tests.append(((lambda n: lambda m: (((m.get("amenities") or {}).get(n["kind"]) or {}).get("distance_m") or 10**9)
                       <= n["max_distance_m"])(n),
                      f"优先距{label} {n['max_distance_m']} 米内", f"Preferred: nearest {label.lower()} within {n['max_distance_m']} m"))
    if abstract:
        for n in params.get("abstract_needs") or []:
            if n.get("strength") == "required":
                continue
            symbol, compare = _SCORE_OPERATORS.get(n.get("operator", "gte"), _SCORE_OPERATORS["gte"])
            label = _attr_label(state, n["attribute"])
            tests.append(((lambda n, compare: lambda m: (v := (m.get("context_scores") or {}).get(n["attribute"])) is not None
                           and compare(v, n["min_score"]))(n, compare),
                          f"优先「{label}」评分 {symbol} {n['min_score']}",
                          f"Preferred: “{label}” score {symbol} {n['min_score']}"))
    if not tests or not metrics:
        return metrics
    ordered = sorted(metrics, key=lambda m: -sum(ok(m) for ok, _, _ in tests))
    shown = ordered[:RESULT_LIMIT]
    for ok, zh, en in reversed(tests):
        hit = sum(ok(m) for m in shown)
        notes.insert(0, _t(state, f"{zh}(不剔除未达到的;展示的 {len(shown)} 套中 {hit} 套满足)",
                           f"{en} (others are not removed; {hit} of the {len(shown)} shown meet it)"))
    return ordered


def _rank_once(state: State) -> dict:
    metrics = state["metrics"]
    params = state.get("params") or {}
    sort_by = params.get("sort_by")
    floor = params.get("min_gross_yield")
    notes = []
    roi_unproven = False
    roi_pool_cut = state.get("search_order") == "roi" and bool(state.get("roi_pool"))

    def _expansion_futile() -> bool:
        zone_req = params.get("school_zone")
        if zone_req and not (state.get("zone_lookup") or {}).get("found"):
            return True
        place = params.get("near_place")
        if place and place.get("max_distance_m"):
            lookup = state.get("place_lookup") or {}
            if lookup.get("error") or not lookup.get("found"):
                return True
        return False

    def _no_match(*args, **kwargs):
        retry = roi_pool_cut and not _expansion_futile()
        if retry:
            cap_note = _t(state, f"候选池是按投资回报率预排序的前 {len(state['metrics'])} 套(已达扩容上限),池外可能还有符合条件的房源",
                          f"The candidate pool is the top {len(state['metrics'])} properties pre-sorted by ROI (expansion cap reached); "
                          f"properties outside it may still match")
            args[1].append(cap_note)
        out = _unmatched_refinement(*args, **kwargs)
        if retry and out.get("turn_notice"):
            out["turn_notice"] += " " + cap_note
        return {**out, "_roi_unproven": True} if retry else out

    typed = params.get("suburb_typed_as")
    if typed and params.get("suburb"):
        notes.append(_t(state,
            f"你输入的是「{typed}」,已按「{params['suburb']}」搜索 —— 如果不是这个区,请改一下",
            f"You typed “{typed}”; searched “{params['suburb']}” instead — change it if that is not the suburb you meant"))

    if params.get("unsupported_asks"):
        notes.append(_t(state, "以下要求本系统没有相应数据,未纳入筛选:",
                        "No data for these, so they were not used as filters: ")
                     + _unsupported_list(state, params["unsupported_asks"]))

    if sort_by in ("gross_yield", "cap_rate", "roi") or floor is not None:
        def _price_suspect(m):
            gap = m.get("predicted_gap")
            return gap is not None and gap > 3 * (m.get("valuation_error_pct") or 0.10)
        suspect = sum(1 for m in metrics if _price_suspect(m))
        if suspect and suspect < len(metrics):
            metrics = [m for m in metrics if not _price_suspect(m)]
            notes.append(_t(state,
                f"已剔除 {suspect} 套售价远低于模型估值的(低出该房型典型误差 3 倍以上,多半是数据异常,"
                f"回报率会被虚高)",
                f"Dropped {suspect} properties priced far below the model estimate (over 3× the typical error "
                f"for the type — usually bad data, which would inflate the yield)"))
        elif suspect:
            notes.append(_t(state,
                f"注意:候选全部售价远低于模型估值(低出该房型典型误差 3 倍以上),未剔除,回报率可能虚高",
                f"Note: every candidate is priced far below the model estimate (over 3× the typical error for the "
                f"type); none were dropped, so the yields may be inflated"))

    if floor is not None:
        kept = [m for m in metrics if m.get("gross_yield") is not None and m["gross_yield"] >= floor]
        if kept:
            metrics = kept
            notes.append(_t(state, f"已筛掉毛租金回报率低于 {floor * 100:.1f}% 的",
                            f"Filtered out gross yields below {floor * 100:.1f}%"))
        else:
            metrics = []
            notes.append(_t(state, f"没有候选达到毛回报率 {floor * 100:.1f}% 的要求",
                            f"No candidate meets the gross yield requirement of {floor * 100:.1f}%"))

    def keep(predicate, zh, en):
        nonlocal metrics
        metrics = [m for m in metrics if predicate(m)]
        notes.append(_t(state, zh, en))

    for need in params.get("amenity_needs") or []:
        if need.get("strength") != "required":
            continue
        kind, limit = need["kind"], need["max_distance_m"]
        label = _kind_label(state, kind)
        keep(lambda m: (m.get("amenities") or {}).get(kind, {}).get("distance_m") is not None
             and m["amenities"][kind]["distance_m"] <= limit,
             f"要求距{label} {limit} 米内", f"Required: nearest {label.lower()} within {limit} m")

    goals = params.get("relative_preferences") or []
    relax = refinement.relaxable_need(params.get("abstract_needs"), goals) if goals else None

    def meets(m, need, floor):
        _, compare = _SCORE_OPERATORS.get(need.get("operator", "gte"), _SCORE_OPERATORS["gte"])
        value = (m.get("context_scores") or {}).get(need["attribute"])
        return value is not None and compare(value, floor)

    for need in params.get("abstract_needs") or []:
        attr, floor = need["attribute"], need["min_score"]
        symbol, compare = _SCORE_OPERATORS.get(need.get("operator", "gte"), _SCORE_OPERATORS["gte"])
        label = _attr_label(state, attr)
        if need is relax:
            loose = refinement.relaxed_floor(need)
            metrics = [m for m in metrics if meets(m, need, loose)]
            continue
        if not goals and need.get("strength") != "required":
            continue
        keep(lambda m: (m.get("context_scores") or {}).get(attr) is not None
             and compare(m["context_scores"][attr], floor),
             f"已筛出「{label}」评分 {symbol} {floor} 的",
             f"Kept only “{label}” scores {symbol} {floor}")

    zone_req = params.get("school_zone")
    if zone_req:
        found = (state.get("zone_lookup") or {}).get("found")
        keep(lambda m: bool(found) and bool(m.get("in_requested_zone")),
             f"要求在「{zone_req['school']}」学区内",
             f"Required: inside the school zone of {zone_req['school']}")
        if not found:
            notes.append(_t(state, "未能解析指定学区,没有将它替换成其他学区",
                            "The requested school zone could not be resolved; no substitute was used"))

    for need in params.get("planning_needs") or []:
        label, predicate = _PLANNING_NEEDS[need]
        keep(lambda m: predicate(m.get("planning") or {}), f"要求{label}",
             f"Required: {i18n.PLANNING_NEEDS_EN.get(need, label)}")

    place = params.get("near_place")
    if place and place.get("max_distance_m"):
        limit = place["max_distance_m"]
        keep(lambda m: (m.get("near_place") or {}).get("distance_m") is not None
             and m["near_place"]["distance_m"] <= limit,
             f"要求距「{place['name']}」在 {limit} 米内",
             f"Required: within {limit} m of {place['name']}")

    if not metrics:
        return _no_match(state, notes)

    if goals:
        base = state.get("refinement_base") or {}
        previous_rows = base.get("metrics") or []
        needs = params.get("abstract_needs")
        new_params, adjust = None, []
        if relax:
            label, floor = _attr_label(state, relax["attribute"]), relax["min_score"]
            loose = refinement.relaxed_floor(relax)
            strict = [m for m in metrics if meets(m, relax, floor)]
            ranked = refinement.relative_rank(strict, goals, previous_rows, needs)
            if len(ranked) < RESULT_LIMIT:
                wide = refinement.relative_rank(metrics, goals, previous_rows, needs)
                if len(wide) > len(ranked):
                    ranked, floor = wide, loose
                    new_params = deepcopy(params)
                    for need in new_params["abstract_needs"]:
                        if need["attribute"] == relax["attribute"]:
                            need["min_score"] = loose
                    adjust.append(_t(state,
                        f"为了继续调整,「{label}」门槛已从 ≥{relax['min_score']} 放宽到 ≥{loose}。"
                        f"这个门槛是系统根据你的描述推断的,不是你设定的分数。「{label}」要求仍然保留。",
                        f"To keep adjusting, the “{label}” threshold was relaxed from ≥{relax['min_score']} to ≥{loose}. "
                        f"That threshold was inferred from your wording, not a score you set. The “{label}” requirement is kept."))
            notes.append(_t(state, f"已筛出「{label}」评分 ≥ {floor} 的", f"Kept only “{label}” scores ≥ {floor}"))
        else:
            ranked = refinement.relative_rank(metrics, goals, previous_rows, needs)
        metrics = ranked
        if not metrics:
            return _no_match(state, notes, relative=True, hint=_tradeoff_hint(state, params, goals))
        metrics = _prefer_amenities(state, metrics, notes, abstract=False)
        labels = [_attr_label(state, g["field"]) if g["field"] in _ABSTRACT_KEYS
                  else ({"price": "价格", "near_place_distance": "到指定地点的距离"}.get(g["field"], g["field"])
                        if not _en(state) else g["field"].replace("_", " ")) for g in goals]
        notes.append(_t(state, "保留原条件,相对上一轮适度调整:" + "、".join(labels),
                        "Original filters retained; adjusted relative to the previous results: " + ", ".join(labels)))
        shift = _score_shift(state, goals, needs, previous_rows, metrics[:RESULT_LIMIT])
        if shift:
            notes.append(shift)
        notes.append(_t(state, "调整幅度由候选分布计算,不是用户指定的分数门槛",
                        "The adjustment uses the candidate distribution, not a user-specified score threshold"))
        if state.get("search_order") == "roi":
            if roi_pool_cut:
                notes.append(_t(state, f"比较范围为按投资回报率预排序的前 {len(state['metrics'])} 套候选,并非全库,符合条件的可能更多",
                                f"Compared within the top {len(state['metrics'])} candidates pre-sorted by ROI, not the whole dataset; more may match"))
            else:
                notes.append(_t(state, f"比较范围为符合条件的全部 {len(state['metrics'])} 套",
                                f"Compared within all {len(state['metrics'])} matching properties"))
        elif len(state["metrics"]) >= CANDIDATE_LIMIT:
            notes.append(_t(state, f"比较范围为语义相关的 {CANDIDATE_LIMIT} 套候选,并非全库",
                            f"Compared within {CANDIDATE_LIMIT} semantically relevant candidates, not the whole dataset"))
        if len(metrics) < RESULT_LIMIT:
            sparse = _t(state, f"只有 {len(metrics)} 套能在保留现有条件的同时完成这次调整。",
                        f"Only {len(metrics)} listing(s) can make this adjustment while keeping your filters.")
            adjust += [sparse] + [x for x in [_tradeoff_hint(state, new_params or params, goals)] if x]
        out = {"metrics": metrics[:RESULT_LIMIT], "more": metrics[:RESULT_LIMIT * 4],
               "batch_offset": 0, "ranking": ";".join(adjust + notes), "turn_notice": None,
               "refinement_base": None}
        if new_params:
            out["params"] = new_params
        return out

    if sort_by in ("predicted_gap", "predicted_gap_neg"):
        def _plausible(m):
            gap = m.get("predicted_gap")
            if gap is None:
                return False
            err = m.get("valuation_error_pct") or 0.10
            return abs(gap) <= 3 * err
        kept = [m for m in metrics if _plausible(m)]
        dropped = len(metrics) - len(kept)
        if kept:
            metrics = kept
            side = "below" if sort_by == "predicted_gap" else "above"
            outside = sum(1 for m in kept if m.get("valuation_position") == side)
            level = next((m.get("valuation_interval_level") for m in kept if m.get("valuation_interval_level")), None)
            if dropped:
                notes.append(_t(state,
                    f"已剔除 {dropped} 套估值差超出模型分辨力的"
                    f"(超过该房型典型误差 3 倍,多半是数据异常而非捡漏)",
                    f"Dropped {dropped} properties whose valuation gap exceeds what the model can "
                    f"resolve (over 3× the typical error for the type — usually bad data, not a bargain)"))
            if level:
                below = side == "below"
                notes.append(_t(state,
                    f"{'剔除后剩下的' if dropped else '符合条件的'} {len(kept)} 套里,只有 {outside} 套售价{'低于' if below else '高于'}模型 "
                    f"{level:.0%} 把握区间{'下限' if below else '上限'},其余差距都在区间内,"
                    f"属于正常波动,不能算{'便宜' if below else '贵'}",
                    f"Of the {'remaining' if dropped else 'matching'} {len(kept)}, only {outside} are priced {'below' if below else 'above'} the "
                    f"model's {level:.0%} interval — the rest are within it, which is normal variation, "
                    f"not a real {'bargain' if below else 'premium'}"))
        else:
            notes.append(_t(state,
                "注意:候选里每一套的估值差都超出模型分辨力,排序结果不可当作捡漏依据",
                "Note: every candidate's valuation gap is beyond what the model can resolve — "
                "this ranking is not evidence of a bargain"))

    if sort_by:
        if sort_by == "price_asc":
            key, reverse = (lambda m: m.get("price") if (m.get("price") or 0) > 0 else None), False
        elif sort_by == "price_desc":
            key, reverse = (lambda m: m.get("price") if (m.get("price") or 0) > 0 else None), True
        elif sort_by == "predicted_gap_neg":
            key, reverse = (lambda m: m.get("predicted_gap")), False
        elif sort_by in _ABSTRACT_KEYS:
            key, reverse = (lambda m: (m.get("context_scores") or {}).get(sort_by)), True
        elif sort_by == "near_place_distance":
            key, reverse = (lambda m: (m.get("near_place") or {}).get("distance_m")), False
        elif sort_by.startswith(NEAREST_PREFIX):
            kind = sort_by[len(NEAREST_PREFIX):]
            key, reverse = (lambda m: ((m.get("amenities") or {}).get(kind) or {}).get("distance_m")), False
        else:
            key, reverse = (lambda m: m.get(sort_by)), True
        usable = [m for m in metrics if key(m) is not None]
        if usable:
            metrics = sorted(usable, key=key, reverse=reverse)
            pool = len(state["metrics"])
            sql_ordered = state.get("search_order") and _SQL_ORDER.get(sort_by) == state.get("search_order")
            roi_complete = sort_by == "roi" and sql_ordered and not state.get("roi_pool")
            if pool < CANDIDATE_LIMIT or roi_complete:
                scope = _t(state, f"在符合条件的全部 {pool} 套里挑", f"chosen from all {pool} matching properties")
            elif sort_by == "roi" and sql_ordered:
                roi_pool = state.get("roi_pool") or {}
                head = metrics[:RESULT_LIMIT * 4]
                consistent = (roi_pool.get("opex_rate") is not None and roi_pool.get("other_costs") is not None and
                              all(investment_metrics(m.get("price"), m.get("annual_rent"), roi_pool["opex_rate"], roi_pool["other_costs"])["roi"] == m.get("roi")
                                  for m in head))
                proven = (roi_certified_prefix([m.get("roi") for m in metrics], roi_pool.get("cutoff"), roi_pool.get("other_costs"))
                          if consistent else 0)
                shown = min(proven, RESULT_LIMIT * 4)
                roi_unproven = consistent and shown < RESULT_LIMIT * 4
                if not consistent:
                    scope = _t(state, f"在按投资回报率预排序的前 {pool} 套候选里挑;预排序与计算所用的假设不一致,未做验证,符合条件的可能更多",
                               f"chosen from the top {pool} candidates pre-sorted by ROI; the assumptions used for the pre-sort and for the "
                               f"calculation differ, so nothing is verified and more may match")
                elif shown >= RESULT_LIMIT:
                    scope = _t(state, f"在按投资回报率预排序的前 {pool} 套候选里挑;已验证前 {shown} 名不会被候选池外的房源超过",
                               f"chosen from the top {pool} candidates pre-sorted by ROI; the top {shown} are verified "
                               f"not to be beatable by any property outside the pool")
                else:
                    scope = _t(state, f"在按投资回报率预排序的前 {pool} 套候选里挑;未能验证候选池外没有更高者,符合条件的可能更多",
                               f"chosen from the top {pool} candidates pre-sorted by ROI; it could not be verified that nothing "
                               f"outside the pool ranks higher, so more may match")
            elif sql_ordered:
                scope = _t(state, "在全库符合条件的房源里挑", "chosen from every matching property")
            else:
                scope = _t(state, f"在语义最相关的 {pool} 套候选里挑,符合条件的可能更多",
                           f"chosen from the {pool} most semantically relevant candidates; more may match")
            label = _sort_label(state, sort_by)
            notes.append(_t(state, f"按{label}排序({scope})",
                            f"Sorted by: {label} ({scope})"))
        else:
            label = _sort_label(state, sort_by)
            notes.append(_t(state,
                f"注意:候选里没有一套能算出{label},已按相关度排序",
                f"Note: “{label}” could not be computed for any candidate; sorted by relevance instead"))
    else:
        notes.append(_t(state, "按语义相关度排序", "Sorted by semantic relevance"))

    metrics = _prefer_amenities(state, metrics, notes)

    return {"metrics": metrics[:RESULT_LIMIT],
            "more": metrics[:RESULT_LIMIT * 4],
            "batch_offset": 0,
            "ranking": ";".join(notes), "turn_notice": None, "refinement_base": None,
            **({"_roi_unproven": True} if roi_unproven else {})}


ROI_EXPAND_CAP = CANDIDATE_LIMIT * 8


def rank(state: State) -> dict:
    current = dict(state)
    out = _rank_once(current)
    extra, expanded = {}, False
    while out.pop("_roi_unproven", False):
        nxt = (current.get("roi_pool") or {}).get("limit", CANDIDATE_LIMIT) * 2
        if nxt > ROI_EXPAND_CAP:
            break
        current.update(search({**current, "roi_limit": nxt}))
        current.update(analyze(current))
        enriched = enrich(current)
        current.update(enriched)
        extra = {k: v for k, v in enriched.items() if k != "metrics"}
        expanded = True
        out = _rank_once(current)
    if expanded:
        extra.update(properties=current["properties"], search_order=current["search_order"], roi_pool=current["roi_pool"])
    if not out.get("turn_notice"):
        parts = _ignored_notes(state) + ([out["ranking"]] if out.get("ranking") else []) + _change_notes(state, out)
        if parts:
            out["ranking"] = ";".join(parts)
    return {**extra, **out}


def _ignored_notes(state: State) -> list[str]:
    if state.get("intent") != "new_search" or not state.get("user_query"):
        return []
    notes = answer_guard.ignored_conditions(state.get("user_query"), state.get("params") or {}, _dataset_suburbs())
    return [_t(state, zh, en) for zh, en in notes]


def _change_notes(state: State, out: dict) -> list[str]:
    base = state.get("refinement_base") or {}
    before, after = (base.get("metrics") or [])[:RESULT_LIMIT], (out.get("metrics") or [])[:RESULT_LIMIT]
    if state.get("intent") != "refine" or not before or not after:
        return []

    def word(old, new, tol):
        if abs(new - old) <= tol:
            return _t(state, "不变", "unchanged")
        return _t(state, "上升", "up") if new > old else _t(state, "下降", "down")

    parts = []
    b, a = refinement.baseline_for(before, "price"), refinement.baseline_for(after, "price")
    if b is not None and a is not None:
        parts.append(_t(state, f"价格中位 ${b:,.0f}→${a:,.0f}({word(b, a, b * 0.005)})",
                        f"median price ${b:,.0f}→${a:,.0f} ({word(b, a, b * 0.005)})"))
    moving = {g["field"] for g in (state.get("params") or {}).get("relative_preferences") or []}
    for need in (state.get("params") or {}).get("abstract_needs") or []:
        field = need["attribute"]
        if field in moving:
            continue
        b, a = refinement.baseline_for(before, field), refinement.baseline_for(after, field)
        if b is not None and a is not None:
            parts.append(_t(state, f"「{_attr_label(state, field)}」中位 {b:.0f}→{a:.0f}({word(b, a, 0.5)})",
                            f"“{_attr_label(state, field)}” median {b:.0f}→{a:.0f} ({word(b, a, 0.5)})"))
    if not parts:
        return []
    return [_t(state, "与上一轮展示的房源相比:", "Compared with the previous results: ")
            + _t(state, ",", ", ").join(parts)]


def present(state: State) -> dict:
    if state.get("turn_notice"):
        return {"metrics": state.get("metrics") or []}
    metrics = state.get("metrics") or []
    if not metrics:
        return {}
    lats = [m.get("latitude") for m in metrics]
    lons = [m.get("longitude") for m in metrics]
    display = nearby.nearest_by_kind_batch(lats, lons, DEFAULT_AMENITY_KINDS)

    zone_rows = zones.zone_for_batch(lats, lons)
    planning_rows = planning.for_batch(lats, lons)

    out = []
    for m, extra, zone, plan in zip(metrics, display, zone_rows, planning_rows):
        entry = dict(m)
        entry["amenities"] = {**extra, **(m.get("amenities") or {})}
        entry["school_zones"] = zone
        entry["planning"] = m.get("planning") or plan
        if entry["planning"]:
            entry["planning"]["summary"] = planning.describe(entry["planning"])
            entry["planning"]["caveat"] = (
                "分区是法律上的**允许**范围,不是对未来的预测;"
                "分区数据是当前的,成交价是 2016–2018 年的,两者不能因果相连")
        entry["crime"] = suburb_stats.crime_for(m.get("suburb"))
        out.append(entry)
    return {"metrics": out}


_EXPLAIN_SYSTEM = """你是房产投资助手。下面会给你一份已经算好的房源数据(JSON)。

你的任务只有一个:用中文写一段简短说明,帮用户理解这几套房怎么选。

绝对铁律:
1. **只能使用给定 JSON 里出现的数字。禁止计算、禁止推测、禁止补充任何未给出的数值。**
   不要自己算平均值、总价、月租、差额、涨幅 —— 一个都不要算。
2. 某个指标的值是 null,就说"数据不足,暂无法计算",不要跳过、更不要编。
   land_size(土地面积)、building_area(建筑面积)是数据集的记录值,null 表示**没有记录**,
   不要说"没有土地",也不要拿房间数去推测面积。area_suspect 里为 true 的那一项是**自相矛盾的记录**
   (页面上标着"记录值存疑"):不要拿它比较大小或下结论,提到时只能说该面积记录存疑、需自行核实。
3. **数字分三类,措辞必须不同,这是本系统最重要的一条规则:**
   - price / bedrooms 等:来自真实成交记录,可以直接陈述。
   - **annual_rent 不是这套房自己的租金**,是一个参考值(周租金基准×52,或推算值),来源看 rent_source。
     提到它必须按 rent_source 区分说法,不许说成"这套房年租金 X":
       · precinct_exact_sheet:"按片区同户型周租金中位数算,年租约 X"
       · precinct_all_properties:"按片区全部房型周租金中位数算"(没有匹配到同户型基准,要点明)
       · region_exact_sheet / region_all_properties:大区基准,是各片区周租金中位数按租赁保证金登记数加权平均,
         **不是大区整体中位数**,也不是这个片区的租金;要点明片区没有匹配到基准
       · assumed_yield:**不是租金基准**,是成交价×假设回报率推算出来的,不许说成官方/DFFH 公布的租金,
         也不许说"该地区没有租金数据"(只是本次查表没匹配到基准),要点明这是推算值
       · rent_source 为 null 或其他值:只说"租金来源不明",不要猜是哪一种
   - gross_yield(毛租金回报率):annual_rent ÷ 这套房的成交价,是**该参考租金水平下的估计**
     (assumed_yield 时是假设值,不是市场信息),可以直接报数,但不要说成这套房实际能收到的回报。
   - 排序口径里的"全部 N 套"指**符合本次条件的** N 套,不是全库,不要写成"全库 N 套"。
   - **noi / cap_rate / roi:建立在假设之上。** 提到它们时必须点明所依据的假设
     (assumptions 字段里有,用户消息里也会给你人话版)。例如:
     "按运营支出占租金 28% 的行业惯例假设测算,净营运收入约 X"。
     绝不能像陈述事实那样说"这套房的 NOI 是 X"。
   - **predicted_price:模型预测值,不是成交价。** 提到它必须带上 80% 把握区间
     (valuation_interval,在模型没见过的成交上验证过覆盖率)。
4. **环境属性(安静/热闹/生活便利/近公园)是 0~100 的相对评分,不是实测量。**
   数据里给了 context_scores(分数)和 context_evidence(算它用的原始证据)。
   提到分数时**必须同时给出至少一条证据**,例如:
     「安静度 82 分 —— 距最近主干道 640 米、周边 300 米内没有酒吧夜店」
   只报分数不给证据是不允许的:分数是我们定的一套加权口径,证据才是可核对的事实。
   分数的含义是**在全库中的相对位置**(82 分 ≈ 比 82% 的房源安静),
   不要说成"绝对很安静"。
5. **关于"低估/高估"要格外克制。** 判断一律看 valuation_position,不看 predicted_gap 的大小:
   - within:售价在模型 80% 把握区间内,**不许**说低估或高估,
     应该说"估值与售价基本相符"。
   - below:**售价低于**区间下限 = **估值高于售价**(模型认为这套偏便宜)。只能这样写:
     "估值明显高于售价"。
   - above:**售价高于**区间上限 = **估值低于售价**(模型认为这套偏贵)。只能这样写:
     "估值明显低于售价"。
     两种情况都要接一句"这只是模型意见,可能反映了模型没看到的因素(如房况、装修、朝向),
     差距特别大时也可能是成交记录本身有误"。**方向不许写反** —— 实测出过把 below 写成
     "估值远低于售价"的错,和卡片上的「估值高于售价」正好相反。
6. **规划分区(planning)是法条,不是评分,也不是预测。** 数据里的 zone 是这块地
   法律上的用途分区,overlays 是压在它上面的额外限制,nearby 是周边分区构成。
   - 可以像陈述事实一样说:"分区 NRZ1(邻里住宅区,限制加密)"、
     "有 HO 历史保护叠加层,改建需额外审批"。
   - **"法律上允许"不等于"一定会发生"。** 周边有允许高密度的地块,只能说
     "法律上允许开发商申请建高密度住宅",**不能**说"将来会盖起高楼"。
     反过来,"周边都是限制加密的分区,法律上不能建高楼"是可以直接说的 ——
     否定结论比肯定结论强得多,这个不对称要保持住。
   - **分区说不了房价会不会涨。** 不许用分区推测升值潜力、租金走势。
   - 分区是**当前**的,而成交价是 2016–2018 年的,不要把两者因果地连起来。
7. 用户消息里会给出**本次结果的排序口径**。如果是按某个指标排的,要在开头
   一句话交代清楚(例如"以下按毛租金回报率从高到低排列")。
   **提到排序方式时只能照这条口径说**:口径是"按语义相关度排序"时,不许说成按价格、
   按离某处远近、按某个评分排 —— 结果实际是怎么排的,说明就怎么说。
   若口径里写明了"在语义最相关的 N 套候选里挑",也要如实带上这个限定,
   不能让用户误以为这是全库最高。
8. 不要提到 JSON、字段名、id 这些技术细节。用"第 N 套""Glen Iris 这套"来指代。
   **"第 N 套"里的 N 必须取该房源的 `display_no` 字段**,不是它在列表里的顺序。
   用户界面上的卡片编号就是 display_no,写错了用户就对不上是哪一套。
   翻到后面几批时 display_no 可能是 11、12……照写即可。
9. 比例类字段给的都是小数(0.042 就是 4.2%)。写成百分比时**保留一位小数**,
   不要写成 4.2105%。换算单位和四舍五入不算计算,允许。

## 输出格式(必须严格遵守)

第一行:**一句话结论**,不超过 40 字,直接说"该怎么选"。
空一行,然后 3~4 条要点,每条以 "- " 开头、不超过 40 字。

**每一套房都必须被提到,一套都不能漏。** 用户看着 5 张卡片,
说明里只谈其中 3 套,剩下两套就成了没人评价的孤儿 —— 他会以为系统没看它们。
条数不够就**合并同类**,不要为了凑数硬写:

  - 第 1、3 套各项居中,没有突出优点也没有明显短板

平庸的房源合并成一句就够了,把篇幅留给真正需要说的那几套。

就这些。不要标题、不要编号、不要收尾总结。

**绝对不要逐套复述卡片上已经有的数字。** 售价、房型、毛回报、各项距离、
安静度分数、风险叠加层 —— 这些用户在房源卡上一眼就能看见,再抄一遍
只会把真正的判断埋掉。用户抱怨过这段说明是"信息地狱",就是这么来的。

同理,**假设口径和模型误差不用在这里重复**:界面上每个依赖假设的数字旁边
都标了假设、每个估值旁边都标了误差区间。你只在**做判断时**需要点明,
例如"第 4 套估值高出售价 16.9%,超出典型误差,但这只是模型意见"。

你要写的是卡片上**没有**的东西:
  - 几套之间该怎么取舍(谁适合什么样的人)
  - 哪一项差异是真的重要,哪一项其实几乎没差别
  - 有没有需要提醒但不显眼的事

**风险和估值差只在它影响取舍时才提。** 卡片上已经有"估值高于售价 16.9%"、
"须做环境审计"这样的标签了,把标签念一遍不是提醒,是噪音 ——
用户问的是安静,你回一句"第 4 套估值高出售价 16.9%",他会觉得莫名其妙。
要提就得连着结论一起提:「最便宜的第 3 套估值反而高出售价 16.9%,买前先看房况」
—— 这才叫提醒,因为它改变了"选最便宜那套"这个决定。

一个好例子:

  前三套安静度几乎一样,按价格和通勤取舍就行。

  - 第 3 套 $400,000 最便宜,距墨大 3.8 km,是通勤与价格的平衡点
  - 但第 3 套估值高出售价 16.9%,若冲着便宜去,买前要先看房况
  - 第 5 套贵一倍且回报最低,只有"更近市区"这一个理由
  - 第 1、2 套各项居中,没有突出优点也没有明显短板

注意最后一条:第 1、2 套没什么可说的,但**必须交代一句**,不能当它们不存在。

回答里每个价格、收益率、估值,都必须能追溯到我给你的这份数据。"""


def _no_result_answer(params: dict, en: bool = False) -> str:
    said = []
    if params.get("suburb"):
        said.append(f"suburb “{params['suburb']}”" if en else f"区域「{params['suburb']}」")
    if params.get("max_price"):
        said.append(f"max ${params['max_price']:,}" if en else f"价格上限 ${params['max_price']:,}")
    if params.get("min_price"):
        said.append(f"min ${params['min_price']:,}" if en else f"价格下限 ${params['min_price']:,}")
    if params.get("bedrooms"):
        said.append(f"{params['bedrooms']} bed" if en else f"{params['bedrooms']} 房")
    if params.get("bathrooms"):
        said.append(f"{params['bathrooms']} bath" if en else f"{params['bathrooms']} 卫")
    if params.get("property_type"):
        said.append(f"type {params['property_type']}" if en else f"类型 {params['property_type']}")
    if params.get("max_distance_cbd_km"):
        said.append(f"within {params['max_distance_cbd_km']:g} km of the CBD" if en
                    else f"距 CBD {params['max_distance_cbd_km']:g} 公里内")

    if en:
        detail = ", ".join(said) if said else "the current filters"
        lines = [f"No properties match ({detail})."]
    else:
        detail = "、".join(said) if said else "当前条件"
        lines = [f"没有找到符合条件的房源({detail})。"]

    typed = params.get("suburb_typed_as")
    if typed and params.get("suburb"):
        lines.append(
            f"Note: you typed “{typed}” and the system searched “{params['suburb']}”. "
            f"If you meant a different suburb, change it and try again." if en else
            f"注意:你输入的是「{typed}」,系统按「{params['suburb']}」搜的。"
            "如果本来就想找别的区,改一下再试。")
    if params.get("unsupported_asks"):
        if en:
            joined = ", ".join(_unsupported_en(a) for a in params["unsupported_asks"])
            lines.append("Also, there is no data for these, so they **never entered the filtering "
                         "at all**: " + joined + ".")
        else:
            lines.append("另外这几项本系统没有数据,**从一开始就没参与筛选**:"
                         + "、".join(params["unsupported_asks"]) + "。")

    if params.get("suburb"):
        lines.append("This dataset covers Melbourne only, so suburbs in other cities are not included. "
                     "Check the suburb name or try another suburb."
                     if en else "本系统的数据集只有墨尔本房源,不含其他城市的区。请核对区名或换个区再试。")
    else:
        lines.append(f"Try relaxing one of the conditions above ({detail})."
                     if en else f"可以放宽上面的某个条件({detail})再试。")
    return "\n".join(lines)


_EXPLAIN_LANG_EN = """

## LANGUAGE OVERRIDE — supersedes any instruction above about writing in Chinese

Write the entire answer in **English**. Every other rule above still applies
unchanged; only the output language differs, plus these three wordings:

- Refer to a property as **"Property N"**, where N is that property's
  `display_no` field — e.g. "Property 1", "Properties 2 and 3", "Properties 2-4".
  Use exactly this form. The interface turns it into a clickable link to the
  matching card, and any other wording will not be recognised.
- Where the rules above say to write 「数据不足,暂无法计算」, write
  "not enough data to calculate".
- Keep the same shape: one conclusion line of at most ~25 words, a blank line,
  then 3-4 bullets each starting with "- " and at most ~25 words.
"""

_CONCEPT_LANG_EN = """

## LANGUAGE OVERRIDE — supersedes any instruction above about writing in Chinese

Answer in **English**, in 3-5 sentences. Every other constraint above still applies.
"""

_CONCEPT_SYSTEM = """你是房产投资助手。用户问的是一个**概念/名词/公式**,不是具体房源。

用中文简短解释(3~5 句),可以给出公式。

三条硬约束:
1. **绝对不要提到任何具体房源的价格、回报率、估值。** 你手上没有房源数据,
   编一个出来就是这个系统最不能容忍的错误。举例说明时用"假设某房 100 万、
   年租金 4 万"这样**明显是举例**的整数,并明说这是举例。
2. **不要给出任何市场统计数字**(如"墨尔本平均回报率是 4%")。你不知道,
   本系统也没有这个数据。要谈典型区间就说"通常在几个百分点之间"这种定性表述。
3. 回答开头必须写一句:"以下是通用金融常识,不是来自本系统的数据。"

如果用户问的其实和房源有关(比如"这几套哪个好"),就说请他明确一下,
不要凭空回答。"""


def _assumption_lines(state, metrics) -> list[str]:
    used = next((m["assumptions"] for m in metrics if m.get("assumptions")), None) or (state.get("assumption_snap") or {}).get("display")
    before = assumptions.snapshot()
    lines = assumptions.describe()
    if used is None or (before == used and assumptions.snapshot() == used):
        return lines
    opex, fees = used.get("opex_rate"), used.get("other_acquisition_costs")
    if opex is None or fees is None:
        return [_t(state, "这批指标计算时的假设与当前进程级假设不同,不要引用具体的运营支出比例或购置开销数值。",
                   "The assumptions used for these metrics differ from the current global ones; do not quote a specific operating-expense "
                   "ratio or acquisition-cost figure.")]
    return [_t(state, f"运营支出按年租金的 {opex:.1%} 计(这批指标计算时用的假设;进程级假设之后已被改动,以此为准)",
               f"Operating expenses at {opex:.1%} of annual rent (the assumption used for these metrics; the global assumption has since changed — use this one)"),
            _t(state, f"印花税之外的购置开销(过户/律师费、验房费等)按 ${fees:,.0f} 计(同上;印花税另按法定税率算)",
               f"Other acquisition costs excluding stamp duty (conveyancing, inspections etc.) of ${fees:,.0f} (same; stamp duty is calculated separately at statutory rates)")]


_EXPLAIN_HIDDEN = ("car_spaces", "latitude", "longitude", "id")


def area_suspect(m: dict) -> dict:
    land, bld = m.get("land_size"), m.get("building_area")
    land = land if isinstance(land, (int, float)) and land > 0 else None
    bld = bld if isinstance(bld, (int, float)) and bld > 0 else None
    apartment = m.get("property_type") == "apartment"
    cbd = m.get("distance_cbd")
    near = isinstance(cbd, (int, float)) and cbd < 20
    return {
        "land": bool(land and ((apartment and land > 10_000) or (near and land > 20_000))),
        "building": bool(bld and (bld < 20 or bld > 2_000 or (land and not apartment and bld > 3 * land))),
    }


def _explain_fact(m: dict) -> dict:
    fact = {k: v for k, v in m.items() if k not in _EXPLAIN_HIDDEN}
    for key in ("land_size", "building_area"):
        if not (isinstance(fact.get(key), (int, float)) and fact[key] > 0):
            fact[key] = None
    fact["area_suspect"] = area_suspect(m)
    return fact


def explain(state: State) -> dict:
    intent = state.get("intent") or "new_search"
    parking_kind = _parking_kind(state.get("user_query"))
    parking_notice = _parking_notice(state, parking_kind)

    if state.get("turn_notice"):
        answer = state["turn_notice"]
        return {"answer": answer, "history": [{"role": "助手", "text": answer}]}

    if intent == "concept":
        try:
            system = _CONCEPT_SYSTEM + (_CONCEPT_LANG_EN if _en(state) else "")
            return {"answer": _ask_streaming(system, state["user_query"], temperature=0.3),
                    "history": [{"role": "助手", "text": "(解释了一个概念)"}]}
        except Exception as exc:
            return {"answer": _t(state, f"(解释生成失败:{exc})",
                                 f"(Could not generate the explanation: {exc})"),
                    "history": [{"role": "助手", "text": "(概念解释失败)"}]}

    metrics = state.get("metrics") or []
    if not metrics:
        if intent == "about_results":
            answer = _t(state, "还没有搜索结果可供追问。请先告诉我你想找什么样的房子。",
                        "There are no results to ask about yet. Tell me what kind of home you are "
                        "looking for first.")
        else:
            answer = _no_result_answer(state.get("params") or {}, _en(state))
            if state.get("ranking"):
                answer += "\n" + state["ranking"].replace(";", "\n")
        if parking_notice:
            answer = parking_notice + "\n" + answer
        return {"answer": answer, "history": [{"role": "助手", "text": answer[:80]}]}

    offset = int(state.get("batch_offset") or 0)
    numbered = [{"display_no": offset + i + 1, **_explain_fact(m)} for i, m in enumerate(metrics)]
    facts = json.dumps(numbered, ensure_ascii=False, indent=2, default=str)
    assumption_lines = "\n".join(f"  - {line}" for line in _assumption_lines(state, metrics))
    context = [f"用户的问题:{state['user_query']}"]
    if intent == "about_results":
        context.append("这是一个**针对上一轮结果的追问**,下面的房源就是上一轮给出的那几套,"
                       "没有重新检索。回答时不要说「为你找到」,要直接回答他问的那一点。")
    else:
        context.append(f"本次结果的排序口径:{state.get('ranking') or '按语义相关度排序'}")
        goals = (state.get("params") or {}).get("relative_preferences") or []
        if goals:
            comparisons = [{"attribute": goal["field"], "direction": goal["direction"],
                            "degree": goal["degree"], "previous_displayed_median": goal["baseline"],
                            "current_displayed_median": refinement.baseline_for(metrics, goal["field"])}
                           for goal in goals]
            context.append("本轮是【相对调整】,不是寻找该属性的最高分或最低价。必须以这条解释口径为准:\n"
                "1. 开头先说明保留哪些原要求,并在上一轮基础上做小幅/适度调整。\n"
                "2. 以下比较数值由程序从实际展示结果计算。引用 previous_displayed_median/current_displayed_median 时必须明确写【这批房源的评分中位数】,不能仅说整体分数或每套提高。分数是系统相对评价,不是实测噪声、全库排名或市场统计。\n"
                "3. 安静等旧条件仍是用户要求,不能说满足它的房源与本轮诉求相反。用户没有改口追求最热闹。\n"
                "4. 结果是符合原条件的局部调整候选,不能说只能在这几套里选、其他地方找不到更热闹的,也不能按分数绝对高低否定这次相对提升。\n"
                "5. 对照实际房源说明取舍,不宣称新列表每套都比旧列表每套更好。\n"
                "仍生效的条件:" + json.dumps(state.get("params") or {}, ensure_ascii=False) +
                "\n程序计算的本轮比较:" + json.dumps(comparisons, ensure_ascii=False))

    if parking_kind:
        context.append("房源自带车位无法核验,不得说任何房源有或没有车位,也不得据此推荐或声称按车位筛选。"
                       "附近公共停车设施、规划 Parking Overlay 和地点名称与房源自带车位不同。"
                       "系统会另给用户核验提示;其余问题照常回答。")

    lookup = state.get("place_lookup")
    if lookup and lookup.get("error"):
        context.append(f"⚠️ {lookup['error']} —— 必须如实告诉用户没找到这个地点,"
                       "不要假装算过到它的距离。")

    ignored = _ignored_notes(state)
    if ignored:
        context.append("⚠️ 以下内容**没有进入本次检索**(程序检测到的,排序口径里也写了)。必须在结论里提一句,"
                       "并且**绝对不能**说这些条件下「没有房源 / 没有合适的」—— 根本没有搜过:\n"
                       + "\n".join(f"  - {n}" for n in ignored))
    if intent == "refine":
        context.append("说到本轮相对上一轮的变化(价格、评分的升降)时,只能引用排序口径里"
                       "「与上一轮展示的房源相比」「评分变化」这两条程序算好的数字,方向照抄;"
                       "排序口径里没有的变化不要说,也不要拿更早几轮的数字比较。")
    comparisons = answer_guard.comparison_facts(numbered, _en(state))
    context.append("程序算好的比较结论(按卡片编号)。凡是「最便宜 / 最贵 / 离 CBD 最近或最远 / 土地最大 / "
                   "唯一有土地记录 / 回报最高或最低 / 最安静」以及估值高于还是低于售价,**只能照这里说**;"
                   "这里没有的维度不要做「最…」「唯一」的比较。程序会逐句核对,对不上的整句会被删掉:\n"
                   + "\n".join(f"  - {line}" for line in comparisons))
    context.append(f"本次测算所依据的假设(提到 NOI / Cap Rate / ROI 时必须点明):\n{assumption_lines}")
    context.append(f"已算好的房源数据:\n{facts}")

    try:
        system = _EXPLAIN_SYSTEM + (_EXPLAIN_LANG_EN if _en(state) else "")
        answer = _ask_streaming(system, "\n\n".join(context), temperature=0.2)
        answer, removed = answer_guard.strip_false_comparisons(answer, numbered)
        if removed and not answer:
            answer = _t(state, "几套之间的比较:\n", "How these compare:\n") + "\n".join(f"- {c}" for c in comparisons)
    except Exception as exc:
        answer = _t(state,
            f"(说明生成失败:{exc};上方房源与指标均为系统计算结果,不受影响)",
            f"(Could not generate the summary: {exc}. The properties and metrics above are "
            f"computed by the system and are unaffected.)")
    if parking_notice:
        answer = parking_notice + "\n" + answer
    return {"answer": answer, "history": [{"role": "助手", "text": answer[:80]}]}


def _route(state: State) -> str:
    return "search" if state.get("intent") in ("new_search", "refine") else "explain"


def build_graph(checkpointer=None):
    builder = StateGraph(State)
    builder.add_node("parse_intent", parse_intent)
    builder.add_node("search", search)
    builder.add_node("analyze", analyze)
    builder.add_node("enrich", enrich)
    builder.add_node("rank", rank)
    builder.add_node("present", present)
    builder.add_node("explain", explain)

    builder.set_entry_point("parse_intent")
    builder.add_conditional_edges("parse_intent", _route,
                                  {"search": "search", "explain": "explain"})
    builder.add_edge("search", "analyze")
    builder.add_edge("analyze", "enrich")
    builder.add_edge("enrich", "rank")
    builder.add_edge("rank", "present")
    builder.add_edge("present", "explain")
    builder.add_edge("explain", END)

    return builder.compile(checkpointer=checkpointer)


MEMORY = MemorySaver()


def new_session(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


GRAPH = build_graph(checkpointer=MEMORY)
