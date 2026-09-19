"""Load designer-authored prompts from the packaged YAML file."""

from pathlib import Path
from string import Template

import yaml

PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts/image_agent.yaml"
BRAND_FIELDS = (
    "name",
    "description",
    "colors",
    "personality",
    "typography",
    "illustration_style",
)


def load_prompts(path=PROMPT_FILE):
    with path.open(encoding="utf-8") as stream:
        prompts = yaml.safe_load(stream)
    keys = {"system", "brand", "chat", "plan", "generate", "evaluate"}
    if (
        not isinstance(prompts, dict)
        or set(prompts) != keys
        or any(
            not isinstance(value, str) or not value.strip()
            for value in prompts.values()
        )
    ):
        raise ValueError(
            "Prompt YAML must contain nonempty system, brand, chat, plan, generate, and evaluate strings"
        )
    template = Template(prompts["brand"])
    if not template.is_valid() or set(template.get_identifiers()) != set(BRAND_FIELDS):
        raise ValueError("Brand template must reference all six supported brand fields")
    return prompts


def brand_brief(brand, prompts):
    return Template(prompts["brand"]).substitute(
        {field: brand.get(field) or "Not specified" for field in BRAND_FIELDS}
    )
