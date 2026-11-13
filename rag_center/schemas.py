from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Page(Input):
    offset: int = Field(0, ge=0, le=1000000)
    limit: int = Field(30, ge=1, le=100)


class KBCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=4000)


class KBRef(Input):
    knowledge_base_id: str


class KBUpdate(KBRef):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=4000)
    expected_revision: int = Field(ge=1)


class KBDelete(KBRef):
    expected_revision: int = Field(ge=1)


class DocRef(Input):
    document_id: str


class DocList(Page):
    knowledge_base_id: str
    name: str = Field("", max_length=240)
    status: str | None = None


class DocCreate(Input):
    knowledge_base_id: str
    name: str = Field(min_length=1, max_length=240)
    text: str | None = Field(None, max_length=200000)
    upload_id: str | None = Field(None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def one_source(self):
        if (self.text is None) == (self.upload_id is None):
            raise ValueError("Exactly one of text or upload_id is required")
        return self


class DocUpdate(DocRef):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=240)
    text: str | None = Field(None, max_length=200000)
    upload_id: str | None = Field(None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def sources(self):
        if self.text is not None and self.upload_id is not None:
            raise ValueError("Cannot supply both text and upload_id")
        if self.name is None and self.text is None and self.upload_id is None:
            raise ValueError("No changes supplied")
        return self


class DocDelete(DocRef):
    expected_revision: int = Field(ge=1)


class DocRead(DocRef):
    offset: int = Field(0, ge=0)
    limit: int = Field(8000, ge=1, le=20000)


class UploadPrepare(Input):
    knowledge_base_id: str
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(gt=0)
    document_id: str | None = None


class Search(Input):
    query: str = Field(min_length=1, max_length=2000)
    knowledge_base_ids: list[str] | None = None
    mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    limit: int = Field(8, ge=1, le=30)


class Excerpt(DocRef):
    chunk_id: str
    context: int = Field(1, ge=0, le=3)


class JobRef(Input):
    job_id: str


class KeyCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=200)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_in_days: int | None = Field(None, ge=1, le=365)


class KeyRef(Input):
    key_id: str
