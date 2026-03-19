from pydantic import BaseModel


class Equation(BaseModel):
    latex: str
    context: str  # surrounding text for understanding
    label: str | None = None


class Figure(BaseModel):
    image_path: str | None = None
    caption: str
    page_number: int


class Table(BaseModel):
    content: str  # markdown table
    caption: str | None = None


class Section(BaseModel):
    title: str
    content: str
    level: int  # heading level (1, 2, 3)
    equations: list[Equation] = []
    figures: list[Figure] = []
    tables: list[Table] = []


class PaperContent(BaseModel):
    title: str
    authors: list[str] = []
    abstract: str = ""
    sections: list[Section] = []
    raw_text: str  # full text fallback
    source_path: str
