#!/usr/bin/env python3
"""Enrich Pokerole's Pokémon database with species-based evolution relationships.

The user's National Pokédex number is NEVER used to infer a relationship.
Matching is performed by English species/form names -> PokéAPI pokemon identifiers,
then by PokéAPI's explicit species evolution graph and form-specific evolution rows.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
REQUIRED_CSV = ("pokemon_species.csv", "pokemon.csv", "pokemon_evolution.csv")
OPTIONAL_CSV = (
    "pokemon_forms.csv", "evolution_triggers.csv", "items.csv", "item_names.csv",
    "moves.csv", "move_names.csv", "locations.csv", "location_names.csv",
    "regions.csv", "region_names.csv", "languages.csv",
)

TRIGGER_ZH = {
    "level-up": "升級",
    "trade": "通訊交換",
    "use-item": "使用道具",
    "shed": "分離進化",
    "spin": "旋轉角色後升級",
    "tower-of-darkness": "完成惡之塔試煉",
    "tower-of-waters": "完成水之塔試煉",
    "three-critical-hits": "在戰鬥中打出 3 次要害",
    "take-damage": "承受指定傷害後前往指定地點",
    "other": "特殊條件",
    "agile-style-move": "使用迅疾招式",
    "strong-style-move": "使用剛猛招式",
    "defeat-other-pokemon": "擊敗指定寶可夢",
    "walk-together": "同行／步行條件",
    "level-up-with-bug": "升級並符合蟲屬性條件",
}

TIME_ZH = {"day": "白天", "night": "夜晚", "dusk": "黃昏", "full-moon": "滿月時"}
TYPE_ZH = {
    "1": "一般", "2": "格鬥", "3": "飛行", "4": "毒", "5": "地面", "6": "岩石",
    "7": "蟲", "8": "幽靈", "9": "鋼", "10": "火", "11": "水", "12": "草",
    "13": "電", "14": "超能力", "15": "冰", "16": "龍", "17": "惡", "18": "妖精",
}

# These entries are transformations, not permanent species evolution.
MEGA_CONDITION = "持有該寶可夢對應的超級石，由訓練家在戰鬥中發動超級進化；戰鬥結束後恢復原形。"
PRIMAL_CONDITIONS = {
    "原始蓋歐卡": "持有藍色寶珠，在戰鬥中發動原始回歸；戰鬥結束後恢復原形。",
    "原始固拉多": "持有紅色寶珠，在戰鬥中發動原始回歸；戰鬥結束後恢復原形。",
}

# Curated conditions for battle-only forms where the CSV marks the form but
# the transformation trigger needs the species' ability/game rule explanation.
# Keys are exact PokeAPI pokemon identifiers, never Pokédex numbers.
BATTLE_FORM_CONDITIONS = {
    "palafin-hero": {
        "text": "特性「全能變身」（Zero to Hero）：海豚俠以平凡型態進入戰鬥後，第一次在未瀕死的情況下被換下場，會轉為全能型態；全能型態維持至該場戰鬥結束，離開戰鬥後恢復平凡型態。",
        "trigger": "zero-to-hero",
        "triggerLabel": "特性觸發／換下場",
        "source": "https://bulbapedia.bulbagarden.net/wiki/Palafin_(Pok%C3%A9mon)",
    },
    "wishiwashi-school": {
        "text": "特性「魚群」（Schooling）：等級至少 20 級時，若進入戰鬥時 HP 高於最大 HP 的 25%，會轉為魚群的樣子；回合結束時 HP 降至最大 HP 的 25% 或以下，會恢復單獨的樣子；若之後 HP 再高於 25%，可再次變為魚群型態。換下場後也會恢復單獨的樣子；在戰鬥外維持單獨的樣子。",
        "trigger": "schooling",
        "triggerLabel": "特性觸發／等級與 HP 條件",
        "source": "https://bulbapedia.bulbagarden.net/wiki/Wishiwashi_(Pok%C3%A9mon)",
    },
    "darmanitan-zen": {
        "text": "必須具隱藏特性「達摩模式」（Zen Mode）：戰鬥中，標準型態的 HP 於回合結束時降至最大 HP 的 50% 或以下，轉為達摩模式；達摩模式的 HP 於回合結束時高於 50%，轉回標準型態。換下場及戰鬥外恢復標準型態。",
        "trigger": "zen-mode",
        "triggerLabel": "特性觸發／HP 門檻",
        "source": "https://bulbapedia.bulbagarden.net/wiki/Zen_Mode_(Ability)",
    },
    "darmanitan-galar-zen": {
        "text": "必須具隱藏特性「達摩模式」（Zen Mode）：伽勒爾達摩狒狒在戰鬥中，標準型態的 HP 於回合結束時降至最大 HP 的 50% 或以下，轉為伽勒爾達摩模式；達摩模式的 HP 於回合結束時高於 50%，轉回伽勒爾標準型態。換下場及戰鬥外恢復伽勒爾標準型態。",
        "trigger": "zen-mode",
        "triggerLabel": "特性觸發／HP 門檻",
        "source": "https://bulbapedia.bulbagarden.net/wiki/Zen_Mode_(Ability)",
    },
}

# Battle-only records sometimes share a species ID with several regional/default records.
# Use exact PokeAPI identifiers to select the matching base form, never the species default blindly.
BATTLE_FORM_BASE_IDENTIFIERS = {
    "palafin-hero": "palafin",
    "wishiwashi-school": "wishiwashi-solo",
    "darmanitan-zen": "darmanitan-standard",
    "darmanitan-galar-zen": "darmanitan-galar-standard",
    "eiscue-noice": "eiscue",
    "mimikyu-busted": "mimikyu-disguised",
    "cramorant-gulping": "cramorant",
    "cramorant-gorging": "cramorant",
    "morpeko-hangry": "morpeko-full-belly",
    "zacian-crowned": "zacian",
    "zamazenta-crowned": "zamazenta",
}

# Minior's core form is not marked battle-only in pokemon_forms.csv, so link this
# explicit same-species pair separately. The user's workbook stores one generic core row;
# the API's red core is used as the representative record, not as a claim that colors switch.
EXPLICIT_FORM_TRANSITIONS = [
    {
        "from": "minior-red-meteor",
        "to": "minior-red",
        "text": "特性「界限盾殼」（Shields Down）：戰鬥中，流星的樣子在回合結束時 HP 降至最大 HP 的 50% 或以下，外殼破裂並露出核心；核心的樣子在回合結束時 HP 高於 50%，恢復流星外殼。核心顏色是個別外觀，不會因這項轉換而改色。",
        "trigger": "shields-down",
        "triggerLabel": "特性觸發／HP 門檻",
        "source": "https://bulbapedia.bulbagarden.net/wiki/Minior",
    }
]

SPECIAL_ALIASES = {
    "nidoran (female)": "nidoran-f",
    "nidoran (male)": "nidoran-m",
    "indeedee(female)": "indeedee-female",
    "indeedee(male)": "indeedee-male",
    "mr. mime (galarian)": "mr-mime-galar",
    "tauros (paldean, combat)": "tauros-paldea-combat-breed",
    "tauros (paldean, blaze)": "tauros-paldea-blaze-breed",
    "tauros (paldean, aqua)": "tauros-paldea-aqua-breed",
    "darmanitan (galarian)": "darmanitan-galar-standard",
    "wormadam/(plant cloak)": "wormadam-plant",
    "wormadam/(trash cloak)": "wormadam-trash",
    "wormadam/(sandy cloak)": "wormadam-sandy",
    "cherrim (overcast)": "cherrim",
    "dialga (altered)": "dialga",
    "palkia (altered)": "palkia",
    "giratina (altered)": "giratina-altered",
    "shaymin (land)": "shaymin-land",
    "tornadus (incarnate)": "tornadus-incarnate",
    "thundurus (incarnate)": "thundurus-incarnate",
    "landorus (incarnate)": "landorus-incarnate",
    "kyurem (black)": "kyurem-black",
    "kyurem (white)": "kyurem-white",
    "keldeo (resolute form)": "keldeo-resolute",
    "meloetta (aria)": "meloetta-aria",
    "meloetta (pirouette)": "meloetta-pirouette",
    "greninja (battle bond)": "greninja-battle-bond",
    "aegislash (shield)": "aegislash-shield",
    "aegislash (blade)": "aegislash-blade",
    "zygarde (10%)": "zygarde-10",
    "zygarde (50%)": "zygarde-50",
    "zygarde (complete)": "zygarde-complete",
    "hoopa (confined)": "hoopa",
    "hoopa (unbound)": "hoopa-unbound",
    "oricorio/(baile style)": "oricorio-baile",
    "oricorio/(pom-pom style)": "oricorio-pom-pom",
    "oricorio/(pa’u style)": "oricorio-pau",
    "oricorio/(sensu style)": "oricorio-sensu",
    "lycanroc/(midday form)": "lycanroc-midday",
    "lycanroc/(dusk form)": "lycanroc-dusk",
    "lycanroc/(midnight form)": "lycanroc-midnight",
    "wishiwashi/(solo form)": "wishiwashi-solo",
    "wishiwashi/(school form)": "wishiwashi-school",
    "minior/(meteor form)": "minior-red-meteor",
    "minior/(core)": "minior-red",
    "necrozma (dusk mane)": "necrozma-dusk",
    "necrozma (dawn wings)": "necrozma-dawn",
    "necrozma (ultra)": "necrozma-ultra",
    "toxtricity(low)": "toxtricity-low-key",
    "toxtricity(high)": "toxtricity-amped",
    "eiscue (ice face)": "eiscue-ice",
    "eiscue (no ice face)": "eiscue-noice",
    "zacian (hero)": "zacian",
    "zacian (crowned)": "zacian-crowned-sword",
    "zamazenta (hero)": "zamazenta",
    "zamazenta (crowned)": "zamazenta-crowned-shield",
    "eternatus(giga)": "eternatus-eternamax",
    "urshifu(rapid)": "urshifu-rapid-strike",
    "urshifu(single)": "urshifu-single-strike",
    "calyrex(ice)": "calyrex-ice-rider",
    "calyrex(shadow)": "calyrex-shadow-rider",
    "ursaluna(moon)": "ursaluna-bloodmoon",
}

REGIONAL_SUFFIX = {
    "alolan": "alola", "galarian": "galar", "hisuian": "hisui", "paldean": "paldea",
}
FORM_SUFFIX = {
    "origin": "origin", "therian": "therian", "incarnate": "incarnate", "primal": "primal",
    "normal": "normal", "attack": "attack", "defense": "defense", "speed": "speed",
    "sky": "sky", "land": "land", "10%": "10", "50%": "50", "complete": "complete",
    "dusk mane": "dusk", "dawn wings": "dawn", "ultra": "ultra", "black": "black", "white": "white",
}


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace("♀", " female ").replace("♂", " male ")
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def candidate_identifiers(p: dict) -> list[str]:
    en = norm_text(p.get("en", ""))
    low = en.lower()
    cn = norm_text(p.get("cn", "")).replace(" ", "")
    if not en:
        return []

    # These records share an English species label, so use the workbook's explicit form name.
    # The Pokédex number is used later only to collect/audit sibling records, not to create links.
    if low == "darmanitan":
        return ["darmanitan-zen"] if "達摩模式" in cn else ["darmanitan-standard"]
    if low == "minior (meteor form)" or "流星的樣子" in cn:
        return ["minior-red-meteor"]
    if low == "minior (core)" or cn.endswith("核心") or "(核心)" in cn:
        return ["minior-red"]

    # Palafin's two Pokerole rows share the same English species label.
    # Use the explicit Chinese form label to select the correct PokeAPI Pokemon record.
    # This is a form-name match, not a National Dex-number inference.
    if low == "palafin":
        if "全能型態" in cn or "英雄型態" in cn or "hero" in cn.lower():
            return ["palafin-hero"]
        if "平凡型態" in cn or "零型態" in cn or "zero" in cn.lower():
            return ["palafin-zero"]

    # Darmanitan's Galarian standard/zen entries share the same English label.
    # Use the Chinese form label to distinguish the API form (never the Pokédex number).
    if low == "darmanitan (galarian)" and "達摩模式" in p.get("cn", ""):
        return ["darmanitan-galar-zen"]
    if low in SPECIAL_ALIASES:
        return [SPECIAL_ALIASES[low]]

    # Dataset's Mega Charizard X/Y English labels are crossed in source data;
    # mega linking is resolved from the Chinese form name, not from the Pokédex number.
    if p.get("stage") == "超級進化" or low.startswith("mega "):
        return []

    # Region-specific forms use the PokeAPI pokemon identifiers, e.g. raichu-alola.
    m = re.match(r"^(.*?)\s*\((Alolan|Galarian|Hisuian|Paldean)\)$", en, re.I)
    if m:
        base, region = m.group(1), m.group(2).lower()
        return [f"{slugify(base)}-{REGIONAL_SUFFIX[region]}"]
    # Special regional breed variants.
    m = re.match(r"^(.*?)\s*\((Paldean),\s*(Combat|Blaze|Aqua)\)$", en, re.I)
    if m:
        return [f"{slugify(m.group(1))}-paldea-{m.group(3).lower()}-breed"]

    # Common parenthesized forms.
    m = re.match(r"^(.*?)\s*\((.*?)\)$", en)
    if m:
        base, form = m.group(1), m.group(2).lower().strip()
        if form in FORM_SUFFIX:
            return [f"{slugify(base)}-{FORM_SUFFIX[form]}"]
        if form.endswith(" form"):
            form = form[:-5].strip()
            form_map = {
                "midday": "midday", "midday form": "midday", "dusk": "dusk", "midnight": "midnight",
                "school": "school", "solo": "solo", "sky": "sky", "land": "land", "resolute": "resolute",
            }
            return [f"{slugify(base)}-{form_map.get(form, slugify(form))}"]
        return [f"{slugify(base)}-{slugify(form)}", slugify(base)]

    # Slash-style form labels used in the workbook.
    m = re.match(r"^(.*?)\s*/\s*\((.*?)\)$", en)
    if m:
        base, form = m.group(1), m.group(2).lower().strip()
        form_map = {
            "plant cloak": "plant", "trash cloak": "trash", "sandy cloak": "sandy",
            "baile style": "baile", "pom-pom style": "pom-pom", "pa'u style": "pau", "sensu style": "sensu",
            "midday form": "midday", "dusk form": "dusk", "midnight form": "midnight",
            "solo form": "solo", "school form": "school", "meteor form": "red-meteor", "core": "red",
        }
        return [f"{slugify(base)}-{form_map.get(form, slugify(form))}", slugify(base)]

    return [slugify(en)]


def fetch_csv(name: str) -> list[dict]:
    url = BASE_URL + name
    req = Request(url, headers={"User-Agent": "PokeRole3-Evolution-Data-Builder/1.0"})
    last_error = None
    for attempt in range(5):
        try:
            with urlopen(req, timeout=45) as response:
                text = response.read().decode("utf-8-sig")
            return list(csv.DictReader(io.StringIO(text)))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"無法下載 PokeAPI CSV：{name} ({last_error})")


def read_csv_data(name: str, csv_dir: Path | None, required: bool = True) -> list[dict]:
    path = csv_dir / name if csv_dir else None
    if path and path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    try:
        return fetch_csv(name)
    except RuntimeError:
        if required:
            raise
        print(f"[WARN] Optional localization CSV unavailable: {name}", file=sys.stderr)
        return []


def int_or_none(v):
    try:
        return int(v) if v not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def first_int(row: dict, *keys: str) -> int | None:
    """Read the first present compatible CSV field name."""
    for key in keys:
        if key in row and row.get(key) not in (None, "", "0"):
            return int_or_none(row.get(key))
    return None


def bool_one(value) -> bool:
    return str(value or "0").strip().lower() in {"1", "true", "yes"}


def build_localized_name_map(name_rows: list[dict], key: str, lang_id: str | None) -> dict[int, str]:
    out = {}
    if not lang_id:
        return out
    for row in name_rows:
        if row.get("local_language_id") == lang_id:
            i = int_or_none(row.get(key))
            if i is not None and row.get("name"):
                out[i] = row["name"]
    return out


def find_zh_hant_language_id(language_rows: list[dict]) -> str | None:
    for row in language_rows:
        identifier = (row.get("identifier") or "").lower().replace("_", "-")
        if identifier in {"zh-hant", "zh-tw", "zh"}:
            return row.get("id")
    return None


def human_identifier(identifier: str) -> str:
    return (identifier or "").replace("-", " ").replace("_", " ").strip().capitalize()


def build_condition(row: dict, lookups: dict, species_name=None, evolved_species_identifier: str = "",
                    parent_pokemon_identifier: str = "", evolved_pokemon_identifier: str = "") -> dict:
    item_map = lookups.get("items", {})
    move_map = lookups.get("moves", {})
    location_map = lookups.get("locations", {})
    region_map = lookups.get("regions", {})
    trigger_map = lookups.get("triggers", {})

    trigger_id = int_or_none(row.get("evolution_trigger_id"))
    trigger = trigger_map.get(trigger_id, "")
    trigger_zh = TRIGGER_ZH.get(trigger, human_identifier(trigger)) if trigger else "特殊條件"
    parts = []

    min_level = int_or_none(row.get("minimum_level"))
    if min_level:
        parts.append(f"等級達到 {min_level} 級")

    if parent_pokemon_identifier == "rockruff-own-tempo":
        parts.append("必須是具特性「我行我素」（Own Tempo）的岩狗狗；此條件對應黃昏的樣子")
    elif parent_pokemon_identifier == "rockruff":
        if evolved_pokemon_identifier == "lycanroc-midday":
            parts.append("通常型態岩狗狗（非我行我素型態）於白天升級")
        elif evolved_pokemon_identifier == "lycanroc-midnight":
            parts.append("通常型態岩狗狗（非我行我素型態）於夜晚升級")

    trigger_item = int_or_none(row.get("trigger_item_id"))
    if trigger_item:
        item = item_map.get(trigger_item, f"道具 #{trigger_item}")
        parts.append(f"使用「{item}」")

    held_item = int_or_none(row.get("held_item_id"))
    if held_item:
        item = item_map.get(held_item, f"道具 #{held_item}")
        parts.append(f"持有「{item}」")

    gender_id = int_or_none(row.get("gender_id"))
    if gender_id == 1:
        parts.append("雌性")
    elif gender_id == 2:
        parts.append("雄性")

    location_id = int_or_none(row.get("location_id"))
    if location_id:
        parts.append(f"在「{location_map.get(location_id, f'地點 #{location_id}')}」進化")

    min_happiness = int_or_none(row.get("minimum_happiness"))
    if min_happiness is not None:
        parts.append(f"親密度至少 {min_happiness}")
    min_beauty = int_or_none(row.get("minimum_beauty"))
    if min_beauty is not None:
        parts.append(f"美麗度至少 {min_beauty}")
    min_affection = int_or_none(row.get("minimum_affection"))
    if min_affection is not None:
        parts.append(f"友好度至少 {min_affection}")

    time_of_day = (row.get("time_of_day") or "").strip().lower()
    if time_of_day:
        parts.append(TIME_ZH.get(time_of_day, f"指定時段：{time_of_day}"))

    known_move = int_or_none(row.get("known_move_id"))
    if known_move:
        parts.append(f"學會招式「{move_map.get(known_move, f'招式 #{known_move}')}」")
    known_move_type = int_or_none(row.get("known_move_type_id"))
    if known_move_type:
        parts.append(f"掌握{TYPE_ZH.get(str(known_move_type), f'屬性 #{known_move_type}')}屬性招式")

    relative = row.get("relative_physical_stats")
    if relative not in (None, ""):
        relative_map = {"1": "攻擊高於防禦", "0": "攻擊等於防禦", "-1": "攻擊低於防禦"}
        parts.append(relative_map.get(str(relative), f"攻防數值關係 {relative}"))

    party_species = int_or_none(row.get("party_species_id"))
    if party_species:
        party_name = species_name(party_species) if species_name else None
        parts.append(f"隊伍中有「{party_name}」" if party_name else f"隊伍中有指定物種（來源 species ID {party_species}）")
    party_type = int_or_none(row.get("party_type_id"))
    if party_type:
        parts.append(f"隊伍中有{TYPE_ZH.get(str(party_type), f'屬性 #{party_type}')}屬性寶可夢")
    trade_species = int_or_none(row.get("trade_species_id"))
    if trade_species:
        trade_name = species_name(trade_species) if species_name else None
        parts.append(f"與「{trade_name}」通訊交換" if trade_name else f"與指定物種交換（來源 species ID {trade_species}）")

    if bool_one(row.get("needs_overworld_rain")):
        parts.append("需要在地圖上遇到下雨天氣")
    if bool_one(row.get("turn_upside_down")):
        parts.append("升級時將裝置倒轉")
    if bool_one(row.get("needs_multiplayer")):
        if evolved_species_identifier == "palafin":
            parts.append("在多人連線的「集友圈（Union Circle）」中升級")
        else:
            parts.append("需要多人連線／共同遊玩")
    if bool_one(row.get("near_special_rock")):
        parts.append("在特殊岩石附近升級")

    region_id = int_or_none(row.get("region_id"))
    if region_id:
        parts.append(f"地區條件：{region_map.get(region_id, f'區域 #{region_id}')}")

    used_move_id = int_or_none(row.get("used_move_id"))
    if used_move_id:
        move = move_map.get(used_move_id, f"招式 #{used_move_id}")
        parts.append(f"使用招式「{move}」")
    min_move_count = int_or_none(row.get("minimum_move_count"))
    if min_move_count:
        parts.append(f"使用指定招式至少 {min_move_count} 次")
    min_steps = int_or_none(row.get("minimum_steps"))
    if min_steps:
        parts.append(f"累積步數至少 {min_steps} 步")
    min_damage = int_or_none(row.get("minimum_damage_taken"))
    if min_damage:
        parts.append(f"累積承受至少 {min_damage} 點傷害")

    if trigger == "trade":
        parts.insert(0, "通訊交換")
    elif trigger == "use-item" and not trigger_item:
        parts.insert(0, "使用進化道具")
    elif trigger == "level-up" and not min_level:
        parts.insert(0, "升級時")
    elif trigger and trigger not in {"level-up", "use-item", "trade"}:
        parts.insert(0, trigger_zh)

    expression = norm_text(row.get("condition_expression", ""))
    if expression:
        # Preserve new PokeAPI conditions verbatim as supplemental source data instead of guessing its grammar.
        parts.append(f"來源補充條件：{expression}")
    nature_mask = norm_text(row.get("nature_bitmask", ""))
    if nature_mask:
        parts.append(f"性格條件代碼：{nature_mask}")
    chance = norm_text(row.get("percentage_chance", ""))
    if chance and chance not in {"0", "0.0", "100", "100.0"}:
        parts.append(f"觸發機率：{chance}%")

    if not parts:
        parts.append(trigger_zh)
    text = "；".join(dict.fromkeys(parts))
    parent_form = first_int(row, "required_pokemon_form_id", "base_form_id")
    child_form = first_int(row, "evolved_pokemon_form_id", "evolved_form_id")
    return {
        "text": text,
        "trigger": trigger,
        "triggerLabel": trigger_zh,
        "timeOfDay": TIME_ZH.get(time_of_day, "") if time_of_day else "",
        "minimumLevel": min_level,
        "versionGroupId": int_or_none(row.get("version_group_id")),
        "requiredPokemonFormId": parent_form,
        "evolvedPokemonFormId": child_form,
        "requiredPokemonIdentifier": parent_pokemon_identifier,
        "evolvedPokemonIdentifier": evolved_pokemon_identifier,
        "conditionExpression": expression,
        "source": "PokeAPI pokemon_evolution.csv",
    }

def resolve_pokemon_reference(reference_id: int | None, expected_species_id: int | None,
                              pokemon_by_id: dict, pokemon_form_to_pokemon_id: dict) -> int | None:
    """Resolve pokemon_evolution form references safely.

    In current PokeAPI pokemon_evolution.csv, base_form_id/evolved_form_id point to
    pokemon.csv IDs. Some normalized snapshots use required_pokemon_form_id /
    evolved_pokemon_form_id; accept either representation by validating species_id.
    """
    if reference_id is None:
        return None
    direct = pokemon_by_id.get(reference_id)
    if direct and (expected_species_id is None or int_or_none(direct.get("species_id")) == expected_species_id):
        return reference_id
    mapped = pokemon_form_to_pokemon_id.get(reference_id)
    mapped_row = pokemon_by_id.get(mapped) if mapped is not None else None
    if mapped_row and (expected_species_id is None or int_or_none(mapped_row.get("species_id")) == expected_species_id):
        return mapped
    return None


def get_parent_candidate(reference_id: int | None, parent_species_id: int | None,
                         entry_by_api_id: dict, pokemon_form_to_pokemon_id: dict,
                         default_entry_by_species: dict, entries_by_species: dict,
                         pokemon_by_id: dict) -> tuple[int | None, int | None]:
    """Return (database index, resolved pokemon.csv ID).

    If a required hidden/API form (e.g. Rockruff with Own Tempo) is absent from the
    user's workbook but that species has exactly one database row, attach the explicit
    form requirement to that single species row. If the workbook has multiple rows
    for that species, do not guess which row represents it.
    """
    pokemon_id = resolve_pokemon_reference(reference_id, parent_species_id, pokemon_by_id,
                                           pokemon_form_to_pokemon_id)
    if pokemon_id is not None:
        exact = entry_by_api_id.get(pokemon_id)
        if exact is not None:
            return exact, pokemon_id
        candidates = entries_by_species.get(parent_species_id, []) if parent_species_id is not None else []
        if len(candidates) == 1:
            return candidates[0], pokemon_id
        return None, pokemon_id
    if reference_id is not None:
        return None, None
    if parent_species_id is None:
        return None, None
    exact_default = default_entry_by_species.get(parent_species_id)
    if exact_default is not None:
        return exact_default, None
    candidates = entries_by_species.get(parent_species_id, [])
    return (candidates[0], None) if len(candidates) == 1 else (None, None)


def parse_js_assignments(text: str) -> tuple[dict[str, object], dict[str, tuple[int, int]]]:
    decoder = json.JSONDecoder()
    values = {}
    spans = {}
    for name in ("POKEROLE_DATABASE_META", "POKEMON_DATABASE", "MOVE_DATABASE", "TRAIT_DATABASE", "LEARNSET_DATABASE"):
        marker = f"const {name} = "
        pos = text.find(marker)
        if pos < 0:
            continue
        start = pos + len(marker)
        value, end = decoder.raw_decode(text[start:])
        values[name] = value
        spans[name] = (start, start + end)
    if "POKEMON_DATABASE" not in values:
        raise ValueError("data.js 找不到 const POKEMON_DATABASE = [...]")
    return values, spans


def make_relation(index: int, records: list[dict], condition: dict, kind: str = "evolution") -> dict:
    p = records[index]
    return {
        "pokemonIndex": index,
        "no": p.get("no", ""),
        "name": norm_text(p.get("cn", "")),
        "englishName": norm_text(p.get("en", "")),
        "image": p.get("image", ""),
        "condition": condition.get("text", ""),
        "conditionDetails": condition,
        "kind": kind,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="site/data.js")
    ap.add_argument("--output", default=None)
    ap.add_argument("--csv-dir", default=None, help="可選：使用本機 CSV 快取資料夾（測試用）")
    args = ap.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path
    csv_dir = Path(args.csv_dir) if args.csv_dir else None

    text = input_path.read_text(encoding="utf-8")
    values, spans = parse_js_assignments(text)
    records = values["POKEMON_DATABASE"]

    print("[INFO] Downloading and reading PokéAPI evolution data...", flush=True)
    species_rows = read_csv_data("pokemon_species.csv", csv_dir, True)
    pokemon_rows = read_csv_data("pokemon.csv", csv_dir, True)
    evolution_rows = read_csv_data("pokemon_evolution.csv", csv_dir, True)
    # Form IDs in pokemon_evolution.csv must be resolved through pokemon_forms.csv.
    # This table is required for safe form-specific matching; do not silently omit it.
    pokemon_form_rows = read_csv_data("pokemon_forms.csv", csv_dir, True)
    trigger_rows = read_csv_data("evolution_triggers.csv", csv_dir, False)
    languages = read_csv_data("languages.csv", csv_dir, False)
    language_id = find_zh_hant_language_id(languages)

    optional_tables = {}
    for name in ("items.csv", "item_names.csv", "moves.csv", "move_names.csv",
                 "locations.csv", "location_names.csv", "regions.csv", "region_names.csv"):
        optional_tables[name] = read_csv_data(name, csv_dir, False)

    def id_name_map(base_file: str, names_file: str, id_column: str) -> dict[int, str]:
        ids = {int_or_none(r.get("id")): r.get("identifier", "")
               for r in optional_tables.get(base_file, []) if int_or_none(r.get("id")) is not None}
        local = build_localized_name_map(optional_tables.get(names_file, []), id_column, language_id)
        return {i: local.get(i) or human_identifier(slug) for i, slug in ids.items() if slug}

    items_by_id = id_name_map("items.csv", "item_names.csv", "item_id")
    moves_by_id = id_name_map("moves.csv", "move_names.csv", "move_id")
    locations_by_id = id_name_map("locations.csv", "location_names.csv", "location_id")
    regions_by_id = id_name_map("regions.csv", "region_names.csv", "region_id")
    triggers = {int_or_none(r.get("id")): r.get("identifier", "")
                for r in trigger_rows if int_or_none(r.get("id")) is not None}
    lookups = {"items": items_by_id, "moves": moves_by_id, "locations": locations_by_id,
               "regions": regions_by_id, "triggers": triggers}

    species_by_id = {int_or_none(r.get("id")): r for r in species_rows if int_or_none(r.get("id")) is not None}
    pokemon_by_id = {int_or_none(r.get("id")): r for r in pokemon_rows if int_or_none(r.get("id")) is not None}
    # pokemon_forms.csv is used to resolve alternate form IDs when a snapshot stores them.
    # The current PokeAPI pokemon_evolution.csv base_form_id/evolved_form_id normally refer
    # directly to pokemon.csv IDs; resolve_pokemon_reference validates either representation.
    pokemon_form_to_pokemon_id = {
        int_or_none(r.get("id")): int_or_none(r.get("pokemon_id"))
        for r in pokemon_form_rows
        if int_or_none(r.get("id")) is not None and int_or_none(r.get("pokemon_id")) is not None
    }
    pokemon_forms_by_pokemon_id = defaultdict(list)
    for form_row in pokemon_form_rows:
        form_pokemon_id = int_or_none(form_row.get("pokemon_id"))
        if form_pokemon_id is not None:
            pokemon_forms_by_pokemon_id[form_pokemon_id].append(form_row)
    pokemon_by_identifier = {r.get("identifier"): r for r in pokemon_rows if r.get("identifier")}

    # Match workbook English species/form names to PokeAPI pokemon identifiers.
    # The user's National Pokédex number is display-only and is NEVER used to make an evolution edge.
    entry_api_id: dict[int, int] = {}
    entry_status: dict[int, str] = {}
    entry_by_api_id: dict[int, int] = {}
    entries_by_species: dict[int, list[int]] = defaultdict(list)
    default_entry_by_species: dict[int, int] = {}
    unmatched = []
    for i, p in enumerate(records):
        candidates = candidate_identifiers(p)
        match = next((pokemon_by_identifier[c] for c in candidates if c in pokemon_by_identifier), None)
        if match is None:
            entry_status[i] = "unmatched"
            unmatched.append({"name": norm_text(p.get("cn", "")),
                              "englishName": norm_text(p.get("en", "")),
                              "stage": p.get("stage", "")})
            continue
        pid = int(match["id"])
        sid = int(match["species_id"])
        entry_api_id[i] = pid
        entry_status[i] = "matched"
        entry_by_api_id[pid] = i
        entries_by_species[sid].append(i)
        if str(match.get("is_default", "0")) == "1":
            default_entry_by_species[sid] = i

    def species_chinese_name(species_id: int) -> str | None:
        default_index = default_entry_by_species.get(species_id)
        if default_index is None:
            candidates = entries_by_species.get(species_id, [])
            default_index = candidates[0] if len(candidates) == 1 else None
        return norm_text(records[default_index].get("cn", "")) if default_index is not None else None

    # Keep Excel's original Pokerole fields. In this workbook, evolutionTime is a Pokerole speed tier
    # (快速／中等／緩慢／－), not the main-series game's day/night clock.
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, p in enumerate(records):
        p["evolvesFrom"] = []
        p["evolvesTo"] = []
        p["formChangeFrom"] = []
        p["formChangeTo"] = []
        p["evolutionMatchStatus"] = entry_status.get(i, "unmatched")
        p["evolutionDataSource"] = "PokeAPI species/evolution graph + 使用者提供的 Pokerole Excel"
        p["evolutionDataGeneratedAtUtc"] = generated_at
        p["evolutionSpeedPokerole"] = p.get("evolutionTime", "")
        p["evolutionExcelItem"] = p.get("evolutionItem", "")
        p["evolutionExcelMethod"] = p.get("evolutionMethod", "")

    evolution_by_child_species = defaultdict(list)
    for row in evolution_rows:
        child_sid = int_or_none(row.get("evolved_species_id"))
        if child_sid is not None:
            evolution_by_child_species[child_sid].append(row)

    edge_seen = set()
    edge_count = 0
    unresolved_edges = []
    for child_sid, rows in evolution_by_child_species.items():
        species_row = species_by_id.get(child_sid)
        parent_sid = int_or_none(species_row.get("evolves_from_species_id")) if species_row else None
        if parent_sid is None:
            continue

        # PokeAPI's public CSV schema has used base_form_id/evolved_form_id (pokemon.csv IDs);
        # some normalized exports use required_pokemon_form_id/evolved_pokemon_form_id.
        # Resolve by checking the expected species ID against both pokemon.csv and pokemon_forms.csv.
        # Select the latest rule set per explicit form pair, retaining parallel conditions.
        grouped_rows = defaultdict(list)
        for row in rows:
            parent_form = first_int(row, "required_pokemon_form_id", "base_form_id") or 0
            child_form = first_int(row, "evolved_pokemon_form_id", "evolved_form_id") or 0
            grouped_rows[(parent_form, child_form)].append(row)

        for (parent_form_id, child_form_id), pair_rows in grouped_rows.items():
            max_version = max((int_or_none(r.get("version_group_id")) or 0) for r in pair_rows)
            selected_rows = [r for r in pair_rows
                             if (int_or_none(r.get("version_group_id")) or 0) == max_version]
            for row in selected_rows:
                row_parent_form = first_int(row, "required_pokemon_form_id", "base_form_id")
                row_child_form = first_int(row, "evolved_pokemon_form_id", "evolved_form_id")
                parent_index, parent_api_id = get_parent_candidate(
                    row_parent_form, parent_sid, entry_by_api_id, pokemon_form_to_pokemon_id,
                    default_entry_by_species, entries_by_species, pokemon_by_id
                )
                child_api_id = resolve_pokemon_reference(row_child_form, child_sid, pokemon_by_id,
                                                          pokemon_form_to_pokemon_id) if row_child_form else None
                if row_child_form is not None:
                    child_index = entry_by_api_id.get(child_api_id) if child_api_id is not None else None
                else:
                    child_index = default_entry_by_species.get(child_sid)
                    if child_index is None:
                        candidates = entries_by_species.get(child_sid, [])
                        child_index = candidates[0] if len(candidates) == 1 else None
                if child_index is None:
                    unresolved_edges.append({"evolvedSpeciesId": child_sid,
                                             "requiredPokemonFormId": row_parent_form,
                                             "evolvedPokemonFormId": row_child_form,
                                             "reason": "evolved form did not map to a user database record"})
                    if parent_index is not None:
                        records[parent_index]["evolutionMatchStatus"] = "unmatched"
                    continue

                if parent_index is None or child_index == parent_index:
                    unresolved_edges.append({"evolvedSpeciesId": child_sid,
                                             "requiredPokemonFormId": row_parent_form,
                                             "evolvedPokemonFormId": row_child_form,
                                             "reason": "required parent form did not map to a user database record"})
                    records[child_index]["evolutionMatchStatus"] = "unmatched"
                    continue

                child_species_identifier = (species_by_id.get(child_sid) or {}).get("identifier", "")
                parent_identifier = (pokemon_by_id.get(parent_api_id) or {}).get("identifier", "") if parent_api_id else ""
                child_identifier = (pokemon_by_id.get(child_api_id) or {}).get("identifier", "") if child_api_id else ""
                cond = build_condition(row, lookups, species_chinese_name, child_species_identifier,
                                       parent_identifier, child_identifier)
                edge_key = (parent_index, child_index, cond["text"])
                if edge_key in edge_seen:
                    continue
                edge_seen.add(edge_key)
                records[child_index]["evolvesFrom"].append(make_relation(parent_index, records, cond))
                records[parent_index]["evolvesTo"].append(make_relation(child_index, records, cond))
                edge_count += 1

    # Add two known main-series conditions that the flat PokeAPI CSV does not fully express.
    # These links are by exact English species identifiers, never by national numbers.
    def entry_for_english(name: str) -> int | None:
        for i, p in enumerate(records):
            if norm_text(p.get("en", "")).lower() == name.lower() and p.get("stage") != "超級進化":
                return i
        return None

    # Wurmple's branch is determined by a hidden personality value/Encryption Constant.
    wurmple_i = entry_for_english("Wurmple")
    for child_name in ("Silcoon", "Cascoon"):
        child_i = entry_for_english(child_name)
        if wurmple_i is not None and child_i is not None:
            condition = {"text": "升至 7 級；分支由隱藏的個性值／加密常數決定，玩家無法自行選擇。",
                         "trigger": "level-up", "triggerLabel": "升級", "timeOfDay": "",
                         "minimumLevel": 7, "source": "Bulbapedia Wurmple evolution data"}
            forward = next((r for r in records[wurmple_i]["evolvesTo"] if r.get("pokemonIndex") == child_i), None)
            reverse = next((r for r in records[child_i]["evolvesFrom"] if r.get("pokemonIndex") == wurmple_i), None)
            branch_note = condition["text"]
            if forward is None:
                forward = make_relation(child_i, records, condition)
                records[wurmple_i]["evolvesTo"].append(forward)
                edge_count += 1
            elif "個性值／加密常數" not in forward.get("condition", ""):
                forward["condition"] = f"{forward.get('condition', '').rstrip('；')}；{branch_note}"
                forward.setdefault("conditionDetails", {})["text"] = forward["condition"]
            if reverse is None:
                reverse = make_relation(wurmple_i, records, condition)
                records[child_i]["evolvesFrom"].append(reverse)
            elif "個性值／加密常數" not in reverse.get("condition", ""):
                reverse["condition"] = f"{reverse.get('condition', '').rstrip('；')}；{branch_note}"
                reverse.setdefault("conditionDetails", {})["text"] = reverse["condition"]

    # Shedinja is an additional/special result when Nincada evolves into Ninjask.
    nincada_i = entry_for_english("Nincada")
    shedinja_i = entry_for_english("Shedinja")
    if nincada_i is not None and shedinja_i is not None:
        condition = {"text": "與土居忍士一同升至 20 級時額外出現；需保留隊伍空位，且背包有一般精靈球（第四世代起需要；第三世代不需要）。",
                     "trigger": "special-evolution", "triggerLabel": "特殊追加進化", "timeOfDay": "",
                     "minimumLevel": 20, "source": "Bulbapedia Nincada/Shedinja evolution data"}
        if not any(r.get("pokemonIndex") == shedinja_i for r in records[nincada_i]["evolvesTo"]):
            records[nincada_i]["evolvesTo"].append(make_relation(shedinja_i, records, condition, "special-evolution"))
            records[shedinja_i]["evolvesFrom"].append(make_relation(nincada_i, records, condition, "special-evolution"))
            edge_count += 1

    # Explicit form transitions whose alternate state is not marked is_battle_only in pokemon_forms.csv.
    for pair in EXPLICIT_FORM_TRANSITIONS:
        source_index = next((i for i, pid in entry_api_id.items()
                             if pokemon_by_id.get(pid, {}).get("identifier") == pair["from"]), None)
        target_index = next((i for i, pid in entry_api_id.items()
                             if pokemon_by_id.get(pid, {}).get("identifier") == pair["to"]), None)
        if source_index is None or target_index is None or source_index == target_index:
            unresolved_edges.append({"fromIdentifier": pair["from"], "toIdentifier": pair["to"],
                                     "reason": "explicit form transition did not map to two distinct workbook rows"})
            continue
        details = {"text": pair["text"], "trigger": pair["trigger"],
                   "triggerLabel": pair["triggerLabel"], "timeOfDay": "", "source": pair["source"],
                   "formTransitionPair": [pair["from"], pair["to"]]}
        if not any(r.get("pokemonIndex") == target_index and r.get("kind") == "battle-form"
                   for r in records[source_index]["formChangeTo"]):
            records[source_index]["formChangeTo"].append(make_relation(target_index, records, details, "battle-form"))
        if not any(r.get("pokemonIndex") == source_index and r.get("kind") == "battle-form"
                   for r in records[target_index]["formChangeFrom"]):
            records[target_index]["formChangeFrom"].append(make_relation(source_index, records, details, "battle-form"))
        records[source_index]["hasBattleFormChanges"] = True
        records[target_index]["hasBattleFormChanges"] = True

    # Temporary transformations are separate from permanent species evolution.
    normalized_cn = {norm_text(p.get("cn", "")).replace(" ", ""): i for i, p in enumerate(records)}
    for i, p in enumerate(records):
        cn = norm_text(p.get("cn", "")).replace(" ", "")
        is_mega = p.get("stage") == "超級進化" or norm_text(p.get("en", "")).lower().startswith("mega ")
        is_primal = cn.startswith("原始") and cn in PRIMAL_CONDITIONS
        if is_mega:
            base_name = cn.removeprefix("超級")
            base_name = re.sub(r"[XY]$", "", base_name)
            base_index = normalized_cn.get(base_name)
            if base_index is None:
                p["evolutionMatchStatus"] = "special-unmatched"
                continue
            condition = MEGA_CONDITION
            if "烈空坐" in cn:
                condition = "學會招式「畫龍點睛」後，在戰鬥中使用該招式觸發超級進化；不需要超級石，戰鬥結束後恢復原形。"
            details = {"text": condition, "trigger": "mega-evolution", "triggerLabel": "戰鬥中形態變化", "timeOfDay": "", "source": "Pokémon Mega Evolution rules; not a permanent species evolution"}
            p["evolutionMatchStatus"] = "special-form"
            p["specialFormKind"] = "Mega Evolution"
            p["specialFormCondition"] = condition
            p["formChangeFrom"].append(make_relation(base_index, records, details, "temporary-form"))
            records[base_index]["formChangeTo"].append(make_relation(i, records, details, "temporary-form"))
        elif is_primal:
            base_name = cn.replace("原始", "", 1)
            base_index = normalized_cn.get(base_name)
            if base_index is None:
                p["evolutionMatchStatus"] = "special-unmatched"
                continue
            condition = PRIMAL_CONDITIONS[cn]
            details = {"text": condition, "trigger": "primal-reversion", "triggerLabel": "戰鬥中形態變化", "timeOfDay": "", "source": "Pokémon Primal Reversion rules; not a permanent species evolution"}
            p["evolutionMatchStatus"] = "special-form"
            p["specialFormKind"] = "Primal Reversion"
            p["specialFormCondition"] = condition
            p["formChangeFrom"].append(make_relation(base_index, records, details, "temporary-form"))
            records[base_index]["formChangeTo"].append(make_relation(i, records, details, "temporary-form"))

    # Link battle-only forms to their same-species default form. These are reversible
    # in-battle form changes, not permanent species evolutions. Exact matching uses
    # the mapped PokeAPI pokemon identifier and its pokemon_forms.csv metadata.
    battle_form_links = 0
    battle_form_unmatched = []
    for pokemon_id, entry_index in entry_by_api_id.items():
        pokemon_row = pokemon_by_id.get(pokemon_id)
        if not pokemon_row:
            continue
        form_rows = pokemon_forms_by_pokemon_id.get(pokemon_id, [])
        battle_rows = [r for r in form_rows if bool_one(r.get("is_battle_only")) and not bool_one(r.get("is_mega"))]
        if not battle_rows:
            continue
        species_id = int_or_none(pokemon_row.get("species_id"))
        base_identifier = BATTLE_FORM_BASE_IDENTIFIERS.get(pokemon_row.get("identifier", ""))
        base_api_row = pokemon_by_identifier.get(base_identifier) if base_identifier else None
        base_index = entry_by_api_id.get(int_or_none(base_api_row.get("id"))) if base_api_row else None
        if base_index is None:
            # Conservative fallback: only use the default species record when it is the
            # sole non-battle-only candidate in this workbook species group.
            candidate_indices = [idx for idx in entries_by_species.get(species_id, []) if idx != entry_index]
            non_battle_candidates = []
            for idx in candidate_indices:
                candidate_pid = next((pid for pid, mapped_idx in entry_by_api_id.items() if mapped_idx == idx), None)
                candidate_forms = pokemon_forms_by_pokemon_id.get(candidate_pid, []) if candidate_pid else []
                if not any(bool_one(fr.get("is_battle_only")) and not bool_one(fr.get("is_mega")) for fr in candidate_forms):
                    non_battle_candidates.append(idx)
            if len(non_battle_candidates) == 1:
                base_index = non_battle_candidates[0]
            elif len(candidate_indices) == 1:
                base_index = candidate_indices[0]
        if base_index is None or base_index == entry_index:
            battle_form_unmatched.append({
                "pokemonIdentifier": pokemon_row.get("identifier", ""),
                "pokemonId": pokemon_id,
                "reason": "battle-only form found but no distinct default-form record in user's database",
            })
            continue

        identifier = norm_text(pokemon_row.get("identifier", ""))
        curated = BATTLE_FORM_CONDITIONS.get(identifier)
        if curated:
            condition_text = curated["text"]
            trigger = curated["trigger"]
            trigger_label = curated["triggerLabel"]
            source = curated["source"]
        else:
            condition_text = (
                "PokéAPI 將此形態標記為戰鬥限定型態（is_battle_only=1），且本資料庫已將它精確對應到同一物種的預設形態。"
                "來源 CSV 未提供此型態的完整觸發條件，因此不自行推定；請以該寶可夢的特性、招式及對應遊戲規則為準。"
            )
            trigger = "battle-only-form"
            trigger_label = "戰鬥限定型態（觸發條件未由 CSV 提供）"
            source = "https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/pokemon_forms.csv"

        details = {
            "text": condition_text,
            "trigger": trigger,
            "triggerLabel": trigger_label,
            "timeOfDay": "",
            "source": source,
            "formIdentifier": identifier,
            "formMetadata": "pokemon_forms.csv:is_battle_only=1",
        }
        # Use API form identity to prevent wrong-form links; do not infer from Dex numbers.
        forward_exists = any(r.get("pokemonIndex") == entry_index and r.get("kind") == "battle-form"
                             for r in records[base_index]["formChangeTo"])
        reverse_exists = any(r.get("pokemonIndex") == base_index and r.get("kind") == "battle-form"
                             for r in records[entry_index]["formChangeFrom"])
        if not forward_exists:
            records[base_index]["formChangeTo"].append(make_relation(entry_index, records, details, "battle-form"))
        if not reverse_exists:
            records[entry_index]["formChangeFrom"].append(make_relation(base_index, records, details, "battle-form"))
        if not (forward_exists and reverse_exists):
            battle_form_links += 1
        records[base_index]["hasBattleFormChanges"] = True
        records[entry_index]["hasBattleFormChanges"] = True
        if not records[entry_index]["evolvesFrom"] and not records[entry_index]["evolvesTo"]:
            records[entry_index]["evolutionMatchStatus"] = "special-form"

    # National Pokédex number is used as a discovery/grouping signal only:
    # it reveals that the workbook contains multiple rows for the same base number.
    # It NEVER creates evolution or form-transition links by itself.
    dex_groups = defaultdict(list)
    for index, p in enumerate(records):
        match = re.match(r"^(\d+)", str(p.get("no", "")).strip())
        if match:
            dex_groups[match.group(1)].append(index)
    dex_group_summaries = []
    api_identifier_by_entry = {idx: pokemon_by_id.get(pid, {}).get("identifier", "")
                               for pid, idx in entry_by_api_id.items()}
    for dex_number, indices in sorted(dex_groups.items(), key=lambda x: int(x[0])):
        if len(indices) <= 1:
            continue
        variants = []
        for idx in indices:
            rec = records[idx]
            variants.append({"pokemonIndex": idx, "no": rec.get("no", ""),
                             "name": norm_text(rec.get("cn", "")),
                             "englishName": norm_text(rec.get("en", "")),
                             "image": rec.get("image", ""),
                             "apiIdentifier": api_identifier_by_entry.get(idx, "")})
        summary = {"dexNumber": dex_number, "count": len(indices), "variants": variants}
        dex_group_summaries.append(summary)
        for idx in indices:
            # Shallow-copy the group and its variant list per record for JSON stability.
            records[idx]["dexFormGroup"] = {"dexNumber": dex_number, "count": len(indices),
                                             "variants": [dict(v) for v in variants]}

    # Sort by names, not numbers. Report forms that could not be matched instead of guessing.
    for p in records:
        for field in ("evolvesFrom", "evolvesTo", "formChangeFrom", "formChangeTo"):
            p[field].sort(key=lambda x: x.get("name", ""))
        if p.get("evolutionMatchStatus") == "matched":
            has_permanent_edges = bool(p["evolvesFrom"] or p["evolvesTo"])
            has_form_edges = bool(p["formChangeFrom"] or p["formChangeTo"])
            if has_form_edges and not has_permanent_edges:
                p["evolutionMatchStatus"] = "special-form"
            elif not has_form_edges and not has_permanent_edges:
                p["evolutionMatchStatus"] = "no-evolution"

    meta = values.get("POKEROLE_DATABASE_META", {})
    matched_count = sum(1 for state in entry_status.values() if state == "matched")
    meta["evolutionRelations"] = edge_count
    meta["battleFormLinks"] = battle_form_links
    meta["dexNumberMultiFormGroups"] = len(dex_group_summaries)
    meta["dexNumberMultiFormRecords"] = sum(g["count"] for g in dex_group_summaries)
    meta["evolutionMatchedPokemon"] = matched_count
    meta["evolutionUnmatchedPokemon"] = len(unmatched)
    meta["evolutionSource"] = "https://github.com/PokeAPI/pokeapi/tree/master/data/v2/csv"
    meta["evolutionGeneratedAtUtc"] = generated_at
    meta["evolutionMatchingRule"] = "Exact English/PokeAPI species-form identifiers + explicit evolution graph; National Dex number groups are audit/display hints only and never create links"

    replacements = []
    for name in ("POKEROLE_DATABASE_META", "POKEMON_DATABASE"):
        if name not in spans:
            continue
        start_pos, end_pos = spans[name]
        new_json = json.dumps(values[name], ensure_ascii=False, separators=(",", ":"))
        replacements.append((start_pos, end_pos, new_json))
    for start_pos, end_pos, new in sorted(replacements, reverse=True):
        text = text[:start_pos] + new + text[end_pos:]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")

    audit = {
        "pokemonRecords": len(records),
        "matchedToPokeAPI": matched_count,
        "unmatchedToPokeAPI": len(unmatched),
        "permanentEvolutionEdges": edge_count,
        "specialFormRecords": sum(1 for p in records if p.get("specialFormKind")),
        "battleFormLinks": battle_form_links,
        "dexNumberMultiFormGroups": len(dex_group_summaries),
        "dexNumberMultiFormRecords": sum(g["count"] for g in dex_group_summaries),
        "dexNumberGroups": dex_group_summaries,
        "unmatchedBattleForms": battle_form_unmatched,
        "statusCounts": {status: sum(1 for p in records if p.get("evolutionMatchStatus") == status)
                         for status in sorted({p.get("evolutionMatchStatus", "") for p in records})},
        "unmatchedRecords": unmatched,
        "unresolvedEvolutionEdges": unresolved_edges,
        "source": meta["evolutionSource"],
        "generatedAtUtc": generated_at,
        "matchingRule": meta["evolutionMatchingRule"],
        "notes": [
            "Excel evolutionTime is retained as Pokerole evolution speed, not a main-series day/night condition.",
            "Mega Evolution and Primal Reversion are kept as temporary form changes, not permanent evolution.",
            "PokeAPI battle-only forms are linked bidirectionally to the exact default form within the same species; these are not permanent evolutions.",
            "Palafin Hero, Wishiwashi Schooling, Darmanitan Zen Mode, and Minior Shields Down use curated ability/game conditions.",
            "Lycanroc forms are permanent evolution branches from Rockruff, not reversible form switches; Own Tempo Rockruff is resolved to the sole Rockruff row only when no separate Own Tempo row exists.",
            "National Pokédex number groups only identify multiple workbook rows for review; exact API identifiers/species/evolution references decide whether a link is evolution or form change.",
            "Unmatched species/forms remain explicitly unmatched; no relationship is guessed from Pokédex numbers.",
        ],
    }
    audit_path = output_path.parent / "evolution-audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Records: {len(records)}", flush=True)
    print(f"[OK] Matched by English species/form name: {matched_count}", flush=True)
    print(f"[OK] Permanent evolution edges: {edge_count}", flush=True)
    print(f"[OK] Special form records (Mega/Primal): {audit['specialFormRecords']}", flush=True)
    print(f"[OK] Battle-only form links: {battle_form_links}", flush=True)
    print(f"[OK] Same National Dex number groups: {len(dex_group_summaries)} groups / {sum(g['count'] for g in dex_group_summaries)} records (grouping only; no links inferred)", flush=True)
    print(f"[WARN] Unmatched form/name records: {len(unmatched)} (listed in evolution-audit.json)", flush=True)
    print(f"[WARN] Unmatched battle forms: {len(battle_form_unmatched)} (listed in evolution-audit.json)", flush=True)
    print(f"[OK] Updated: {output_path}", flush=True)


if __name__ == "__main__":
    main()
