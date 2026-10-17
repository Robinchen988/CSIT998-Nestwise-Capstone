SOURCES = {
    "train_station": {
        "match": ("railway", ('station',)),
        "zh": "火车站", "overpass": 'nwr["railway"="station"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 258},
    "tram_stop": {
        "match": ("railway", ('tram_stop',)),
        "zh": "电车站", "overpass": 'nwr["railway"="tram_stop"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 1630},
    "bus_stop": {
        "match": ("highway", ('bus_stop',)),
        "zh": "公交站", "overpass": 'nwr["highway"="bus_stop"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 15787},
    "airport": {
        "match": ("aeroway", ('aerodrome',)),
        "zh": "机场", "overpass": 'nwr["aeroway"="aerodrome"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 26},

    "kindergarten": {
        "match": ("amenity", ('kindergarten', 'childcare')),
        "zh": "幼儿园/托儿所",
        "overpass": 'nwr["amenity"~"^(kindergarten|childcare)$"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 1242},
    "primary_school": {
        "match": ("amenity", ("school",)),
        "zh": "小学", "overpass": 'nwr["amenity"="school"]', "school_level": "primary",
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 1253},
    "secondary_school": {
        "match": ("amenity", ("school",)),
        "zh": "中学", "overpass": None, "school_level": "secondary",
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 587},
    "university": {
        "match": ("amenity", ('university', 'college')),
        "zh": "大学/学院", "overpass": 'nwr["amenity"~"^(university|college)$"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 189},
    "library": {
        "match": ("amenity", ('library',)),
        "zh": "图书馆", "overpass": 'nwr["amenity"="library"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 209},

    "hospital": {
        "match": ("amenity", ('hospital',)),
        "zh": "医院", "overpass": 'nwr["amenity"="hospital"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 156},
    "pharmacy": {
        "match": ("amenity", ('pharmacy',)),
        "zh": "药房", "overpass": 'nwr["amenity"="pharmacy"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 725},

    "bank": {
        "match": ("amenity", ('bank',)),
        "zh": "银行", "overpass": 'nwr["amenity"="bank"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 462},
    "supermarket": {
        "match": ("shop", ('supermarket',)),
        "zh": "超市", "overpass": 'nwr["shop"="supermarket"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 1043},
    "mall": {
        "match": ("shop", ('mall',)),
        "zh": "购物中心", "overpass": 'nwr["shop"="mall"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 309},
    "police": {
        "match": ("amenity", ('police',)),
        "zh": "警察局", "overpass": 'nwr["amenity"="police"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 128},
    "gym": {
        "match": ("leisure", ('fitness_centre',)),
        "zh": "健身房", "overpass": 'nwr["leisure"="fitness_centre"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 599},
    "sports_centre": {
        "match": ("leisure", ('sports_centre',)),
        "zh": "体育中心", "overpass": 'nwr["leisure"="sports_centre"]',
        "geometry": "point", "measure": "nearest", "user_facing": True, "count": 819},

    "park": {
        "match": ("leisure", ('park', 'garden')),
        "zh": "公园绿地", "overpass": 'nwr["leisure"~"^(park|garden)$"]',
        "geometry": "area", "measure": "nearest", "user_facing": True, "count": 16785},
    "beach": {
        "match": ("natural", ('beach',)),
        "zh": "海滩", "overpass": 'nwr["natural"="beach"]',
        "geometry": "area", "measure": "nearest", "user_facing": True, "count": 223},

    "major_road": {
        "match": ("highway", ('motorway', 'trunk', 'primary')),
        "zh": "主干道", "overpass": 'way["highway"~"^(motorway|trunk|primary)$"]',
        "geometry": "line", "measure": "nearest", "user_facing": False, "count": 18885},
    "secondary_road": {
        "match": ("highway", ('secondary',)),
        "zh": "次干道", "overpass": 'way["highway"~"^(secondary)$"]',
        "geometry": "line", "measure": "nearest", "user_facing": False, "count": 12849},
    "tertiary_road": {
        "match": ("highway", ('tertiary',)),
        "zh": "三级路", "overpass": 'way["highway"="tertiary"]',
        "geometry": "line", "measure": "nearest", "user_facing": False, "count": 33326},
    "tram_line": {
        "match": ("railway", ('tram',)), "exclude": ("service", "*"),
        "zh": "电车线", "overpass": 'way["railway"="tram"]',
        "geometry": "line", "measure": "nearest", "user_facing": False, "count": 1032},
    "railway": {
        "match": ("railway", ('rail',)), "exclude": ("service", "*"),
        "zh": "铁路线", "overpass": 'way["railway"="rail"]',
        "geometry": "line", "measure": "nearest", "user_facing": False, "count": 2517},
    "industrial": {
        "match": ("landuse", ('industrial',)),
        "zh": "工业用地", "overpass": 'way["landuse"="industrial"]',
        "geometry": "area", "measure": "nearest", "user_facing": False, "count": 1351},
    "substation": {
        "match": ("power", ('substation',)),
        "zh": "变电站", "overpass": 'nwr["power"="substation"]',
        "geometry": "point", "measure": "nearest", "user_facing": False, "count": 833},
    "fuel": {
        "match": ("amenity", ('fuel',)),
        "zh": "加油站", "overpass": 'nwr["amenity"="fuel"]',
        "geometry": "point", "measure": "nearest", "user_facing": False, "count": 959},
    "cemetery": {
        "match": ("landuse", ('cemetery',)),
        "zh": "墓地", "overpass": 'nwr["landuse"="cemetery"]',
        "geometry": "area", "measure": "nearest", "user_facing": False, "count": 79},

    "nightlife": {
        "match": ("amenity", ('bar', 'pub', 'nightclub')),
        "zh": "酒吧夜店",
        "overpass": 'nwr["amenity"~"^(bar|pub|nightclub)$"]',
        "geometry": "point", "measure": "count", "radius_m": 300,
        "user_facing": False, "count": 1186},
    "shop": {
        "match": ("shop", "*"), "extra_match": ("amenity", ("cafe", "restaurant", "fast_food")),
        "zh": "商店餐饮",
        "overpass": 'nwr["shop"];nwr["amenity"~"^(cafe|restaurant|fast_food)$"]',
        "geometry": "point", "measure": "count", "radius_m": 800,
        "user_facing": False, "count": 28931},
}

RESAMPLE_M = 50

PROPERTY_EVIDENCE = {
    "distance_cbd_km": {"zh": "距 CBD", "unit": "公里"},
    "size_m2": {"zh": "面积", "unit": "㎡"},
}

SUBURB_EVIDENCE = {
    "crime_rate_per_100k": {"zh": "所在 LGA 罪案率", "unit": "起/10万人"},
}

BY_TYPE_KEYS = ("size_m2",)


def evidence_key(kind: str) -> str:
    source = SOURCES[kind]
    if source["measure"] == "count":
        return f"{kind}_{source['radius_m']}m"
    return f"{kind}_m"


def evidence_label(key: str) -> tuple[str, str]:
    for kind, source in SOURCES.items():
        if evidence_key(kind) == key:
            if source["measure"] == "count":
                return f"{source['radius_m']} 米内{source['zh']}", "家"
            return f"距最近{source['zh']}", "米"
    meta = PROPERTY_EVIDENCE.get(key) or SUBURB_EVIDENCE.get(key)
    return (meta["zh"], meta["unit"]) if meta else (key, "")


USER_FACING_KINDS = tuple(k for k, v in SOURCES.items() if v["user_facing"])


ATTRIBUTES = {
    "quiet": {
        "zh": "安静",
        "synonyms": ["安静", "清静", "不吵", "僻静", "别靠马路", "别靠铁路",
                     "怕吵", "睡眠浅", "闹中取静", "清净"],
        "parts": {"major_road_m": ("far", 0.26), "secondary_road_m": ("far", 0.15),
                  "tertiary_road_m": ("far", 0.10), "railway_m": ("far", 0.14),
                  "tram_line_m": ("far", 0.07), "nightlife_300m": ("few", 0.18),
                  "industrial_m": ("far", 0.10)},
        "note": "远离主干道、次干道、三级路、铁路与电车线和工业区,周边酒吧夜店少",
    },
    "lively": {
        "zh": "热闹",
        "synonyms": ["热闹", "繁华", "有人气", "烟火气", "夜生活", "年轻人多",
                     "别太冷清", "市口好"],
        "parts": {"shop_800m": ("many", 0.55), "nightlife_300m": ("many", 0.45)},
        "note": "周边商店餐饮与酒吧夜店密集",
    },
    "convenient": {
        "zh": "生活便利",
        "synonyms": ["生活便利", "买东西方便", "配套齐全", "楼下就有超市",
                     "吃饭方便", "日常采买方便"],
        "parts": {"shop_800m": ("many", 0.50), "supermarket_m": ("near", 0.30),
                  "pharmacy_m": ("near", 0.20)},
        "note": "步行范围内商店餐饮多、离超市和药房近",
    },
    "shopping": {
        "zh": "购物方便",
        "synonyms": ["购物", "逛街", "商场", "购物中心", "买衣服", "shopping"],
        "parts": {"mall_m": ("near", 0.60), "supermarket_m": ("near", 0.40)},
        "note": "离购物中心与超市近",
    },
    "green": {
        "zh": "近公园绿地",
        "synonyms": ["公园", "绿化", "绿地", "遛弯", "散步", "遛狗",
                     "推婴儿车", "看得到树"],
        "parts": {"park_m": ("near", 1.0)},
        "note": "离公园或绿地近",
    },
    "beach_access": {
        "zh": "近海",
        "synonyms": ["海边", "海滩", "近海", "看海", "海景", "beach"],
        "parts": {"beach_m": ("near", 1.0)},
        "note": "离海滩近",
    },
    "transport": {
        "zh": "通勤方便",
        "synonyms": ["通勤", "上班方便", "交通便利", "近地铁", "近火车站",
                     "近电车", "tram", "公共交通", "不想开车", "离公司近"],
        "parts": {"train_station_m": ("near", 0.40), "tram_stop_m": ("near", 0.25),
                  "bus_stop_m": ("near", 0.15), "distance_cbd_km": ("near", 0.20)},
        "note": "离火车站、电车站、公交站近,且离 CBD 近",
    },
    "school_access": {
        "zh": "教育配套",
        "synonyms": ["附近有学校", "小孩上学", "近幼儿园", "近中学",
                     "孩子读书", "上学方便", "教育配套"],
        "parts": {"primary_school_m": ("near", 0.40), "secondary_school_m": ("near", 0.28),
                  "kindergarten_m": ("near", 0.22), "library_m": ("near", 0.10)},
        "note": "离中小学、幼儿园与图书馆近(只衡量距离,不代表学校质量 —— 本系统没有排名数据)",
    },
    "medical": {
        "zh": "医疗方便",
        "synonyms": ["医疗", "近医院", "看病方便", "养老", "老人住", "就医"],
        "parts": {"hospital_m": ("near", 0.65), "pharmacy_m": ("near", 0.35)},
        "note": "离医院与药房近",
    },
    "fitness": {
        "zh": "运动健身",
        "synonyms": ["健身", "运动", "健身房", "球场", "游泳", "跑步"],
        "parts": {"gym_m": ("near", 0.50), "sports_centre_m": ("near", 0.50)},
        "note": "离健身房与体育中心近",
    },
    "spacious": {
        "zh": "宽敞",
        "synonyms": ["宽敞", "大一点", "面积大", "够住", "别太挤",
                     "三代同堂", "活动空间"],
        "parts": {"size_m2": ("many", 1.0)},
        "note": "面积在同房型中偏大(有建筑面积用建筑面积,否则用地块面积)",
    },
    "family": {
        "zh": "适合家庭",
        "synonyms": ["适合家庭", "带孩子", "一家人住", "family friendly",
                     "小孩成长", "有孩子"],
        "parts": {"primary_school_m": ("near", 0.28), "park_m": ("near", 0.20),
                  "size_m2": ("many", 0.20), "major_road_m": ("far", 0.20),
                  "kindergarten_m": ("near", 0.12)},
        "note": "离小学、幼儿园和公园近,面积大,远离主干道",
    },
    "away_industry": {
        "zh": "远离工业与污染源",
        "synonyms": ["别靠工厂", "工业区", "污染", "变电站", "高压线",
                     "加油站", "环境干净"],
        "parts": {"industrial_m": ("far", 0.50), "substation_m": ("far", 0.30),
                  "fuel_m": ("far", 0.20)},
        "note": "远离工业用地、变电站与加油站",
    },
    "low_crime": {
        "zh": "治安较好",
        "synonyms": ["治安", "安全", "犯罪率低", "安全性", "社会治安", "太平"],
        "parts": {"crime_rate_per_100k": ("few", 1.0)},
        "note": ("所在 LGA 的每 10 万人罪案率较低(维州罪案统计局官方数据)。"
                 "**粒度是 LGA 级不是 suburb 级**,同一个 LGA 内所有房源同分;"
                 "且数据是近期的、房源是 2016–2018 年的,只能当相对指标看"),
    },
    "away_cemetery": {
        "zh": "远离墓地",
        "synonyms": ["墓地", "公墓", "陵园", "别挨着墓地", "忌讳"],
        "parts": {"cemetery_m": ("far", 1.0)},
        "note": "远离墓地(全区仅 79 处,分位分辨率有限)",
    },
}
ATTRIBUTE_KEYS = tuple(ATTRIBUTES)

CONFLICTS = (("quiet", "lively"),)

UNSUPPORTED = {
    "采光朝向": "数据集没有朝向、楼层、窗户信息",
    "装修房况": "数据集没有装修状况、房屋成色的信息",
    "房龄新旧": "建成年份虽在库中,但检索层不返回该字段,线上拿不到",
    "学校排名": "有学校位置和**招生学区边界**,但没有任何排名、评级或教学质量数据",
    "街道或小区级治安": ("注意:**普通的「治安好」是支持的**,用 low_crime 属性。"
                        "这里指的是比 LGA 更细的街道/小区级治安 —— 官方 suburb 级表"
                        "只有原始案件数、没有人口分母,拿计数跨区比较推不出结论"),
    "分类型犯罪": ("注意:**普通的「治安好」是支持的**,用 low_crime 属性。"
                   "这里指的是按入室盗窃、暴力犯罪等**分类**的可比指标 —— 只有总体罪案率"),
    "空气与噪音实测": "只有到噪音源的距离,没有任何实测分贝或空气质量数据",
    "物业与邻居": "数据集没有物业费、管理质量或住户构成的信息",
    "户型格局": "只有房间数量,没有户型图、面宽、得房率等信息",
    "楼层视野": "数据集没有楼层、朝向或视野信息",
    "升值潜力": "只有 2016–2018 年的成交价,没有足以支撑趋势预测的时间跨度",
    "步行/驾车时间": ("所有距离都是**直线距离**,没有路网,算不出步行或驾车时间。"
                      "「离 X 近」可以答(直线),「步行 N 分钟到 X」不能答"),
}


def all_evidence_keys() -> tuple[str, ...]:
    return (tuple(evidence_key(k) for k in SOURCES)
            + tuple(PROPERTY_EVIDENCE) + tuple(SUBURB_EVIDENCE))


def used_evidence_keys() -> set[str]:
    return {key for spec in ATTRIBUTES.values() for key in spec["parts"]}


_INDENT = " " * 21


def prompt_block() -> str:
    return "\n".join(
        f"{_INDENT}{name:<15}{spec['zh']}:" + "、".join(spec["synonyms"])
        for name, spec in ATTRIBUTES.items())


def unsupported_block() -> str:
    return "\n".join(f"{_INDENT}{k:<16}-> {v}" for k, v in UNSUPPORTED.items())


def amenity_kinds_block() -> str:
    return "\n".join(f"{_INDENT}{k:<18}{SOURCES[k]['zh']}" for k in USER_FACING_KINDS)


def sort_labels() -> dict[str, str]:
    return {k: f"「{v['zh']}」评分从高到低" for k, v in ATTRIBUTES.items()}
