"""What the model is allowed to decide, and nothing beyond it.

The schema is the contract. A structured output the model cannot step outside
of is a stronger guarantee than any instruction in a prompt, so the query text
is simply not a field here -- there is no way for a suggestion to come back
carrying a rewritten query, because there is nowhere to put one.

What it may decide: what a model is called, which layer it belongs in, how it
is materialized, a sentence describing it, and which columns look like keys
worth testing. Every one of those is reversible in the review, and none of
them changes what the query computes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

LAYERS = ("staging", "intermediate", "marts")
MATERIALIZATIONS = ("view", "table", "incremental", "ephemeral")


class StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class ColumnTest(StrictModel):
    """A column the model thinks is a key."""

    name: str = Field(description="The column exactly as the query selects it.")
    unique: bool = Field(description="Every row has a different value.")
    not_null: bool = Field(description="No row leaves it empty.")
    reason: str = Field(
        description="Why this looks like a key, in one short clause.",
    )


class ModelSuggestion(StrictModel):
    """The model's opinion about one uploaded query."""

    #: Echoed back so a suggestion can be matched to what it is about. The
    #: model is told to copy it exactly; anything it invents is discarded.
    candidate_key: str = Field(
        description="Copy the candidate_key of the query this is about, exactly.",
    )
    name: str = Field(
        description=(
            "A dbt model name: lower case, digits and underscores, starting "
            "with a letter. Keep the name the query already uses unless it is "
            "unusable or actively misleading."
        ),
    )
    # Which layer a model belongs in, and how it is stored, are not here on
    # purpose. Both follow from what a query reads and what reads it, which
    # the parse already knows, so both are decided rather than asked. A field
    # a model cannot answer is a field it cannot answer wrongly.
    description: str = Field(
        description=(
            "One sentence saying what a row of this model is. Empty if the "
            "query does not make that clear -- a guess is worse than silence."
        ),
    )
    tests: list[ColumnTest] = Field(
        default_factory=list,
        description=(
            "At most two columns, and only ones the query plainly selects. A "
            "test on a column that is not there fails the build for a reason "
            "that has nothing to do with the data."
        ),
    )


class ImportSuggestions(StrictModel):
    """Everything the model has to say about one upload."""

    models: list[ModelSuggestion]
    #: Shown to the person reviewing, not acted on.
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "At most three short warnings worth a reader's attention: a query "
            "that looks like it was meant to be incremental, an obvious "
            "cartesian join, a table that may be a typo. Say nothing rather "
            "than filling this."
        ),
    )
