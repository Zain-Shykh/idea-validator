import pytest

from critic.services.names import missing_names, names_in


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("OpenQuiz already dominates this market", ["OpenQuiz"]),
        ("Quizlet offers flashcards.", ["Quizlet"]),
        ("Students struggle. Many Quizlet users complain.", ["Quizlet"]),
        ("Google Classroom lacks offline mode", ["Google Classroom"]),
        ("Unlike Anki, it syncs", ["Anki"]),
        ("study.com charges teachers", ["study.com"]),
        ("It is weaker than Kahoot's quiz mode", ["Kahoot"]),
        ('The market has one leader: "Quizlet"', []),  # sentence start, no product verb
        ("Anki and Anki again, like Anki", ["Anki"]),
        ("The app has no AI tutor and no SaaS API", []),
        ("Teachers want offline quizzes on Android and iOS", []),
        ("Students cram. They want planners.", []),
        ("", []),
    ],
)
def test_names(text: str, names: list[str]) -> None:
    assert names_in(text) == names


def test_mentions_are_whole_words_in_any_case() -> None:
    texts = ["the QUIZFORGE tool, by study.com", "github"]
    assert missing_names(["QuizForge", "study.com", "GitHub"], texts) == []
    assert missing_names(["Quiz", "Forge"], texts) == ["Quiz", "Forge"]


def test_a_name_must_be_in_one_text_not_split_over_two() -> None:
    assert missing_names(["Google Classroom"], ["Google", "Classroom"]) == ["Google Classroom"]
