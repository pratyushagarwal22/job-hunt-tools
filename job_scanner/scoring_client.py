"""Score one job against the candidate with a single Anthropic call to a scoring tool.

force_tool_choice=True forces the tool call; False uses tool_choice auto with a strict tool
(for models that reject a forced tool_choice). A response without the tool call is an error.
"""

import logging
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_TOOL_NAME = "record_job_score"


class ScoreResult(BaseModel):
    match_score: float = Field(
        ge=1.0, le=10.0, description="Fit score from 1.0 to 10.0, one decimal place."
    )
    title: str = Field(description="Job title as stated in the posting.")
    company: str = Field(description="Hiring company name.")
    location: str = Field(
        description="Job location as stated, including remote/hybrid/onsite. 'Unknown' if not stated."
    )
    visa_mention: str = Field(
        description=(
            "What the posting says about visa sponsorship, citizenship or security clearance. "
            "'Not mentioned' if nothing."
        )
    )
    core_requirements: str = Field(
        description="Short comma-separated list of the main requirements (years, skills, degree)."
    )
    reason: str = Field(description="One-line reason for the score.")

    @field_validator("match_score", mode="before")
    @classmethod
    def _one_decimal(cls, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return round(value, 1)
        return value


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


def _tool_schema() -> dict:
    # Strict tools need additionalProperties=false and reject numeric minimum/maximum;
    # the 1.0-10.0 range is enforced by ScoreResult validation instead.
    schema = ScoreResult.model_json_schema()
    schema["additionalProperties"] = False
    for prop in schema.get("properties", {}).values():
        prop.pop("minimum", None)
        prop.pop("maximum", None)
    return schema


TOOL_SCHEMA = _tool_schema()
TOOL_INSTRUCTION = "Always respond by calling the scoring tool."

SYSTEM_PROMPT = """\
You score how well one job posting fits one candidate. Record your answer by calling the \
{tool_name} tool exactly once.

<candidate>
{candidate_md}
</candidate>

<profile>
{profile_yaml}
</profile>

Scoring:
- Follow the ai_evaluation rules in the profile above.
- Hard blockers -> score 1 to 2. These override every score floor:
  - The posting EXPLICITLY says no visa sponsorship, or US citizens only. If the posting does \
not mention visas, that is NOT a blocker. Requiring a security clearance is also a blocker.
  - The job is based outside the US.
- Location within the US is never a penalty.
- Only use facts stated in the posting. Do not guess requirements, benefits or sponsorship policy.
- match_score uses one decimal place. reason is a single line.

Score calibration:
Typical cases for an early-career software engineering candidate. If anything here conflicts \
with the profile's ai_evaluation rules, the profile rules take precedence.
- 9-10: Strong match on both role and level. A new grad, entry-level or 0-2 years software, \
forward deployed, founding, product or AI engineer role whose core stack and problem domain \
overlap heavily with experience the candidate has actually demonstrated. No hard blocker, and \
little or nothing in the requirements that the candidate clearly lacks.
- 7-8: Good match. Right role family and level, with solid overlap on the core skills but some \
real gaps, such as a framework, domain or tool the candidate has not used, or a preferred \
qualifications list that is only partly met. Also typical for roles asking for 1-3 years where \
the candidate's projects, internships or work history plausibly cover it.
- 6: The floor for early-career roles in the target role families. Use it when role and level \
fit but the skill overlap is thin, the posting is vague, or there is too little information to \
judge more precisely.
- 3-5: A clear level or role mismatch without a hard blocker. The posting clearly requires 3+ \
years AND targets a mid, senior, staff or principal level; or the work is mostly outside \
general software engineering (for example QA-only, IT support, sales engineering) or centered on \
a specialty the candidate shows no evidence for (for example embedded firmware or chip design).
- 1-2: A hard blocker applies, or the job is not an engineering role at all (for example sales, \
recruiting, marketing or operations).

Applying the bands:
- Pick the band first, then move within it by how strong the overlap is. Use decimals; do not \
default to round numbers.
- Experience asks like "1+ years", "2+ years", "0-3 years" or "X years or equivalent \
experience" are compatible with an early-career candidate and never push a target-family role \
below 6 on their own. Only score below 6 for experience when the posting clearly requires 3+ \
years AND is aimed at a mid, senior, staff or principal level.
- Missing information is not a negative. If the posting is silent on visas, location details, \
years of experience or tech stack, do not lower the score for it.
- A senior-sounding title with entry-level requirements is scored by its requirements, and an \
entry-level title with senior requirements is scored by its requirements too.
- The reason should name the main factor behind the score (fit, gap or blocker) in plain words, \
without restating the candidate's personal details.

Example jobs per band (illustrative only; always score against the actual candidate and profile):
- 9-10 example: "Software Engineer, New Grad" at a startup. Builds backend APIs and internal tools \
in the same languages and cloud platform the candidate has shipped real work with. Asks for a CS \
degree or equivalent and 0-1 years. No visa or location restrictions stated.
- 7-8 example: "Software Engineer I" on a payments team. Asks for 1-2 years, a language the \
candidate knows well, plus a framework and a database they have not used. Mentions distributed \
systems experience as a plus, which the candidate only partly shows.
- 6 example: "Product Engineer" at a small company. Lists generic duties ("build features end to \
end, work with design") and a long tool list with little detail. Role and level fit, but there \
is not enough signal to judge stronger overlap.
- 3-5 example: "Software Engineer II, Platform" requiring 4+ years running production \
infrastructure at scale and owning on-call for a mid-level team. Right field, wrong level. A \
"Senior Software Engineer" asking 6+ years lands lower in this band.
- 1-2 example: "Software Engineer, Defense Systems" requiring an active security clearance, or \
any role stating "must be a US citizen" or "we do not sponsor visas", or a role based in a \
country outside the US. A sales, recruiting or account management role also lands here.

Snippet-only inputs:
- Some postings arrive as only a search result title and a short snippet instead of the full \
description. Treat them as incomplete, not as weak.
- Score mainly from the title, the level it implies and whatever requirements the snippet \
actually states. Do not fill gaps with assumptions about the stack, years or sponsorship policy.
- A target-family early-career title with no disqualifying detail in the snippet usually lands \
at 6 to 7. Go higher only when the snippet itself shows strong overlap; go lower only when the \
snippet itself states a level mismatch or a hard blocker.
- For fields the snippet does not cover, use "Unknown" (or "Not mentioned" for visa_mention) \
rather than guessing, and say in the reason that the score is based on a snippet.
"""

SNIPPET_NOTE = """\
NOTE: The full job description could not be fetched. Below is only a search result title and \
snippet. Score from what is there, do not invent requirements that are not stated, and treat \
unstated details (visa, years of experience, location) as unknown rather than as blockers.\
"""


def score_job(
    jd_text: str,
    jd_source: str,
    candidate_md: str,
    profile_yaml: str,
    model: str,
    max_tokens: int,
    max_jd_chars: int,
    force_tool_choice: bool,
    title_hint: str = "",
    company_hint: str = "",
) -> tuple[ScoreResult, Usage]:
    """force_tool_choice=False is for models that reject a forced tool_choice (Sonnet/Opus 5.5)."""
    truncated = len(jd_text) > max_jd_chars
    jd_text = jd_text[:max_jd_chars]

    tool = {
        "name": _TOOL_NAME,
        "description": "Record the job's fit score and extracted fields.",
        "input_schema": TOOL_SCHEMA,
    }
    if force_tool_choice:
        tool_choice = {"type": "tool", "name": _TOOL_NAME}
    else:
        tool["strict"] = True
        tool_choice = {"type": "auto"}

    response = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT.format(
                    tool_name=_TOOL_NAME, candidate_md=candidate_md, profile_yaml=profile_yaml
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": _user_message(
                    jd_text, jd_source, title_hint, company_hint, force_tool_choice
                ),
            }
        ],
        tools=[tool],
        tool_choice=tool_choice,
    )
    usage = response.usage
    cache_creation = usage.cache_creation_input_tokens or 0
    cache_read = usage.cache_read_input_tokens or 0
    logger.info(
        "Scored job (source=%s, jd_chars=%d, truncated=%s): input_tokens=%d "
        "cache_creation_input_tokens=%d cache_read_input_tokens=%d output_tokens=%d",
        jd_source,
        len(jd_text),
        truncated,
        usage.input_tokens,
        cache_creation,
        cache_read,
        usage.output_tokens,
    )
    if cache_creation == 0 and cache_read == 0:
        logger.warning(
            "Prompt cache not used (cache_creation and cache_read both 0); "
            "the cached system prompt is likely below the model's cache minimum"
        )

    tool_input = next(
        (
            block.input
            for block in response.content
            if block.type == "tool_use" and block.name == _TOOL_NAME
        ),
        None,
    )
    if tool_input is None:
        raise ValueError(f"No {_TOOL_NAME} tool call in response (stop_reason={response.stop_reason})")
    tokens = Usage(
        input=usage.input_tokens or 0,
        output=usage.output_tokens or 0,
        cache_read=cache_read,
        cache_write=cache_creation,
    )
    try:
        return ScoreResult.model_validate(tool_input), tokens
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}"
            for e in exc.errors(include_input=False)
        )
        raise ValueError(f"Invalid score result: {problems}") from None


def _user_message(
    jd_text: str, jd_source: str, title_hint: str, company_hint: str, force_tool_choice: bool
) -> str:
    parts = []
    if jd_source == "snippet":
        parts.append(SNIPPET_NOTE)
    if title_hint:
        parts.append(f"Known job title (use as-is): {title_hint}")
    if company_hint:
        parts.append(f"Known company (use as-is): {company_hint}")
    parts.append(f"<job_posting>\n{jd_text}\n</job_posting>")
    if not force_tool_choice:
        parts.append(TOOL_INSTRUCTION)
    return "\n\n".join(parts)
