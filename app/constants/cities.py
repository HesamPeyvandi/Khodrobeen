# Mapping between Divar's URL slug for a city and its Persian display name.
#
# NOTE: Divar occasionally renames or adds city slugs. If a city you need is
# missing, or a slug stops working, open https://divar.ir in a browser,
# switch to that city, and read the slug from the address bar
# (https://divar.ir/s/<slug>/car) and add it below.

CITIES: dict[str, str] = {
    "tehran": "تهران",
    "mashhad": "مشهد",
    "isfahan": "اصفهان",
    "shiraz": "شیراز",
    "tabriz": "تبریز",
    "karaj": "کرج",
    "ahvaz": "اهواز",
    "qom": "قم",
    "kermanshah": "کرمانشاه",
    "urmia": "ارومیه",
    "rasht": "رشت",
    "zahedan": "زاهدان",
    "kerman": "کرمان",
    "arak": "اراک",
    "yazd": "یزد",
    "ardabil": "اردبیل",
    "bandar-abbas": "بندرعباس",
    "zanjan": "زنجان",
    "sanandaj": "سنندج",
    "qazvin": "قزوین",
    "khorramabad": "خرم‌آباد",
    "gorgan": "گرگان",
    "sari": "ساری",
    "hamedan": "همدان",
    "bojnourd": "بجنورد",
    "birjand": "بیرجند",
    "bushehr": "بوشهر",
    "ilam": "ایلام",
    "shahrekord": "شهرکرد",
    "semnan": "سمنان",
    "yasuj": "یاسوج",
}


def city_name(slug: str) -> str:
    return CITIES.get(slug, slug)


def is_valid_city(slug: str) -> bool:
    return slug in CITIES
