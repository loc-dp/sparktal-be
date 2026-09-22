from dataclasses import dataclass

from django.contrib.postgres.search import SearchQuery
from django.core.exceptions import ValidationError

from apps.candidates.models import SEARCH_CONFIG

MAX_TOKENS = 12
SYNTAX_ERROR = "Check the Boolean syntax."
OPERATORS = {"AND", "OR", "NOT"}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


def _fail() -> ValidationError:
    return ValidationError(SYNTAX_ERROR)


def lex(query: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(query):
        char = query[index]
        if char.isspace():
            index += 1
            continue
        if char in "()":
            tokens.append(Token(char, char))
            index += 1
            continue
        if char == '"':
            end = query.find('"', index + 1)
            if end < 0:
                raise _fail()
            phrase = query[index + 1 : end].strip()
            if not phrase:
                raise _fail()
            tokens.append(Token("phrase", phrase))
            index = end + 1
            continue
        end = index
        while end < len(query) and not query[end].isspace() and query[end] not in '()"':
            end += 1
        raw = query[index:end]
        if not raw:
            raise _fail()
        tokens.append(Token("op" if raw in OPERATORS else "word", raw))
        index = end
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.position = 0

    def peek(self) -> Token | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise _fail()
        self.position += 1
        return token

    def is_op(self, name: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "op" and token.value == name

    def parse(self) -> SearchQuery:
        expression = self.or_expression()
        if self.peek() is not None:
            raise _fail()
        return expression

    def or_expression(self) -> SearchQuery:
        left = self.and_expression()
        while self.is_op("OR"):
            self.take()
            left = left | self.and_expression()
        return left

    def and_expression(self) -> SearchQuery:
        left = self.unary()
        while True:
            token = self.peek()
            if token is None or token.kind == ")" or self.is_op("OR"):
                return left
            if self.is_op("AND"):
                self.take()
            left = left & self.unary()

    def unary(self) -> SearchQuery:
        token = self.take()
        if token.kind == "op":
            if token.value != "NOT":
                raise _fail()
            return ~self.unary()
        if token.kind == "(":
            inner = self.or_expression()
            closing = self.peek()
            if closing is None or closing.kind != ")":
                raise _fail()
            self.take()
            return inner
        if token.kind == ")":
            raise _fail()
        return SearchQuery(
            token.value,
            config=SEARCH_CONFIG,
            search_type="phrase" if token.kind == "phrase" else "plain",
        )


def parse_boolean(query: str) -> SearchQuery | None:
    tokens = lex(query or "")
    if not tokens:
        return None
    if len(tokens) > MAX_TOKENS:
        raise _fail()
    return _Parser(tokens).parse()
