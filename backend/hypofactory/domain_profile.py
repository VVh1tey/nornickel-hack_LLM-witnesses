"""Профиль предметной области — параметризует LLM-часть пайплайна (промпты,
entity_types для LightRAG), чтобы подключить новую область (металлургия,
полимеры, композиты...) можно было конфигом, а не правкой промптов по всему
коду. Выбирается через DOMAIN_PROFILE в .env (см. config.py).

ВАЖНО (честно про границы этого решения): это закрывает только LLM-часть
(экстракция сущностей, формулировка "ты эксперт по..." в промптах). Детерминированный
анализатор входных данных (analysis/tails_analyzer.py — правила конкретно под
Excel-схему "класс крупности × минеральная форма × элемент") НЕЛЬЗЯ сделать
конфигом без потери смысла: для другого домена нужен СВОЙ анализатор с другой
входной схемой. Для этого — registry в analysis/registry.py: новый домен
подключается через новый модуль-анализатор + запись в DOMAIN_PROFILES здесь,
без правок остального пайплайна (generator/verification/ranker/roadmap уже
работают с общими схемами TargetSpec/Hypothesis, домен не различают).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DomainProfile:
    name: str
    expert_role: str  # подставляется в системные промпты generator/verification/ranker/roadmap
    entity_types: list[str] = field(default_factory=list)  # для LightRAG-экстракции графа
    analyzer: str = ""  # ключ в analysis/registry.py ANALYZERS
    # (сфера, ключевые слова) для группировки few-shot в generator.py — та же
    # логика "не копировать пропорции тем в примерах", но словарь ключевых слов
    # свой для каждого домена (у обогащения — классификация/измельчение/
    # флотация/автоматизация, у другого домена были бы другие сферы).
    spheres: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    other_sphere: str = "Прочее"


DOMAIN_PROFILES: dict[str, DomainProfile] = {
    "obogashchenie": DomainProfile(
        name="Обогащение полезных ископаемых",
        expert_role=(
            "эксперт-исследователь в области обогащения полезных ископаемых "
            "(флотация, измельчение, классификация, реагентный режим)"
        ),
        entity_types=[
            "минерал",
            "металл",
            "реагент",
            "оборудование",
            "технологическая_операция",
            "параметр_режима",
            "показатель",
            "класс_крупности",
        ],
        analyzer="tails",
        spheres=[
            ("Классификация/грохочение/гидроциклоны", ("классифика", "гидроциклон", "грохот", "сит", "насад")),
            ("Измельчение/дробление", ("мельниц", "футеровк", "дробилк", "измельч", "шар", "гал")),
            ("Флотация/реагентный режим", ("флотаци", "реагент", "пульп", "чан", "агитаци")),
            ("Автоматизация/контроль параметров", ("автоматиза", "контрол", "регулирован", "гранулометри")),
        ],
    ),
}

DEFAULT_PROFILE_KEY = "obogashchenie"


def get_profile(key: str) -> DomainProfile:
    if key not in DOMAIN_PROFILES:
        raise ValueError(
            f"Неизвестный профиль домена: {key!r}. Доступные: {list(DOMAIN_PROFILES)}. "
            "Новый домен добавляется записью в DOMAIN_PROFILES (backend/hypofactory/domain_profile.py) "
            "+ анализатором в analysis/registry.py — без правок остального пайплайна."
        )
    return DOMAIN_PROFILES[key]
