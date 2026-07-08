"""Parser for the OPTIMADE filter language.

Parses an OPTIMADE filter string into a nested tuple abstract syntax tree
(the "ojf" format), e.g.::

    ('AND', ('HAS_ALL', ('=', '='), ('Identifier', 'elements'),
             (('String', 'Ga'), ('String', 'Ti'))),
            ('OR', ('=', ('Identifier', 'nelements'), ('Number', '3')),
                   ('=', ('Identifier', 'nelements'), ('Number', '2'))))
"""

from functools import lru_cache
from importlib.resources import files
from typing import Any

from . import _miniparser
from ._miniparser import ParserError, ParserSyntaxError

__all__ = ["parse_optimade_filter", "parse_optimade_filter_raw", "ParserError", "ParserSyntaxError", "FilterAst"]

FilterAst = tuple[Any, ...]


class ParserInternalError(Exception):
    """Raised when the parse tree has an unexpected shape (a bug, not bad user input)."""


def parse_optimade_filter(filter_string: str, verbosity: int | Any = 0) -> FilterAst:
    # To get diagnostic output, pass, e.g., verbosity=LogVerbosity(0, parser_verbosity=5)

    parse_tree = parse_optimade_filter_raw(filter_string, verbosity)

    return optimade_parse_tree_to_ojf(parse_tree)


def parse_optimade_filter_raw(filter_string: str, verbosity: int | Any = 0) -> tuple[Any, ...]:
    return _miniparser.parser(_optimade_parser_ls(), filter_string, verbosity=verbosity)


@lru_cache(maxsize=1)
def _optimade_parser_ls() -> dict[str, Any]:
    grammar = files("httk.optimade.filter").joinpath("optimade_filter_grammar.ebnf").read_text(encoding="utf-8")

    # Keywords
    literals = [
        "AND",
        "NOT",
        "OR",
        "KNOWN",
        "UNKNOWN",
        "IS",
        "CONTAINS",
        "STARTS",
        "ENDS",
        "WITH",
        "LENGTH",
        "HAS",
        "ALL",
        "ONLY",
        "EXACTLY",
        "ANY",
        " ",
        "\t",
        "\n",
        "\r",
    ]

    # Token definitions from the OPTIMADE specification appendix
    tokens = {
        "Operator": r'<|<=|>|>=|=|!=',
        "Identifier": "[a-z_][a-z_0-9]*",
        "String": r'"[^"\\]*(?:\\.[^"\\]*)*"',
        "Number": r"[-+]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][-+]?[0-9]+)?",
        "OpeningBrace": r"\(",
        "ClosingBrace": r"\)",
        "Dot": r'\.',
        "Colon": r":",
        "Comma": r",",
    }
    partial_tokens = {
        "Number": r"[-+]?[0-9]+\.?[0-9]*[eE]?[-+]?[0-9]*",
    }
    # We don't need these, because they are handled on higher level
    # by the token definitions.
    skip = [
        "EscapedChar",
        "UnescapedChar",
        "Punctuator",
        "Exponent",
        "Sign",
        "Digits",
        "Digit",
        "Letter",
        "Operator",
        "UppercaseLetter",
        "LowercaseLetter",
        "OpeningBrace",
        "Dot",
        "ClosingBrace",
        "Comma",
        "Colon",
    ]

    return _miniparser.build_ls(
        ebnf_grammar=grammar,
        start='Filter',
        ignore=' \t\n',
        tokens=tokens,
        partial_tokens=partial_tokens,
        literals=literals,
        verbosity=0,
        skip=skip,
        remove=[')', '('],
        simplify=[],
    )


def optimade_parse_tree_to_ojf(ast: tuple[Any, ...]) -> FilterAst:
    if ast[0] != 'Filter':
        raise ParserInternalError("Parse tree does not start with a Filter node: " + str(ast[0]))
    return optimade_parse_tree_to_ojf_recurse(ast[1])


def _fix_const(node: tuple[Any, ...]) -> tuple[Any, ...]:
    if node[0] == 'Property':
        assert node[1][0] == 'Identifier'
        return node[1]
    elif node[0] == 'String':
        assert node[1][-1] == '"'
        assert node[1][0] == '"'
        return ('String', node[1][1:-1])
    else:
        return node


def optimade_parse_tree_to_ojf_recurse(node: tuple[Any, ...], recursion: int = 0) -> FilterAst:

    tree: list[Any] = [None]
    pos = tree
    arg: int | None = 0

    if node[0] in ['Expression', 'ExpressionClause', 'ExpressionPhrase', 'Comparison']:
        n = node[1:]
        if n[0][0] == "NOT":
            assert arg is not None
            pos[arg] = ['NOT', None]
            pos = pos[arg]
            arg = 1
            n = tuple(n[1:])
        for nn in n:
            if nn[0] in [
                'Expression',
                'ExpressionClause',
                'ExpressionPhrase',
                'PropertyFirstComparison',
                'ConstantFirstComparison',
                'PredicateComparison',
                'Comparison',
            ]:
                assert arg is not None and pos[arg] is None
                pos[arg] = optimade_parse_tree_to_ojf_recurse(nn, recursion=recursion + 1)
            elif nn[0] in ["AND", "OR"]:
                assert arg is not None and pos[arg] is not None
                pos[arg] = [nn[0], tuple(pos[arg]), None]
                pos = pos[arg]
                arg = 2
            elif nn[0] in ["OpeningBrace", "ClosingBrace"]:
                pass
            else:
                raise ParserInternalError("Filter simplify on invalid ast: " + str(nn[0]))
    elif node[0] in ['PropertyFirstComparison', 'ConstantFirstComparison']:
        assert arg is not None and pos[arg] is None

        left: tuple[Any, ...]
        if node[0] == 'PropertyFirstComparison':
            assert node[1][0] == 'Property'
            left = ('Identifier',) + tuple([x[1] for x in node[1][1:] if x[0] != 'Dot'])
        else:
            assert node[1][0] == 'Constant'
            left = _fix_const(node[1][1])
        if node[2][0] == "ValueOpRhs":
            assert node[2][1][0] == 'Operator'
            op = node[2][1][1]
            assert node[2][2][0] == 'Value'
            right = _fix_const(node[2][2][1])
            pos[arg] = (op, left, right)
            arg = None
        elif node[2][0] == "FuzzyStringOpRhs":
            assert node[2][1][0] in ['CONTAINS', 'STARTS', 'ENDS']
            op = node[2][1][1]
            if node[2][1][0] in ['STARTS', 'ENDS'] and node[2][2][0] == "WITH":
                right = node[2][3]
            else:
                right = node[2][2]
            assert right[0] == 'Value' or right[0] == 'Property'
            if right[0] == 'Value':
                right = _fix_const(right[1])
            pos[arg] = (op, left, right)
            arg = None
        elif node[2][0] == "KnownOpRhs":
            assert node[2][1][0] == 'IS'
            op = node[2][1][1]
            assert node[2][2][0] in ['KNOWN', 'UNKNOWN']
            op += "_" + node[2][2][0]
            assert node[1][0] == 'Property'
            operand = ('Identifier',) + tuple([x[1] for x in node[1][1:] if x[0] != 'Dot'])
            pos[arg] = (op, operand)
            arg = None
        elif node[2][0] == "SetOpRhs":
            assert node[2][1][0] == 'HAS'
            if node[2][2][0] == 'Operator':
                assert len(node[2]) == 4
                op = "HAS"
                inop = node[2][2][1]
                right = node[2][3][1]
                pos[arg] = (op, (inop,), left, (right,))
            elif len(node[2]) == 3:
                op = "HAS_ALL"
                assert node[2][2][0] == 'Value'
                right = _fix_const(node[2][2][1])
                pos[arg] = (op, ('=',), left, (right,))
            elif len(node[2]) == 4:
                assert node[2][2][0] in ['ONLY', 'ALL', 'EXACTLY', 'ANY']
                assert node[2][3][0] == 'ValueList'
                op = "HAS_" + node[2][2][0]
                inop = None
                rights: list[Any] = []
                inops: list[Any] = []
                for x in node[2][3][1:]:
                    if x[0] == 'Operator':
                        assert inop is None
                        inop = x[1]
                    elif x[0] == 'Value':
                        inops += ['=' if inop is None else inop]
                        rights += [_fix_const(x[1])]
                        inop = None
                pos[arg] = (op, tuple(inops), left, tuple(rights))
            else:
                raise ParserInternalError(
                    "Filter simplify on invalid ast, unexpected number of components in set op: " + str(node[2])
                )
            arg = None
        elif node[2][0] == "SetZipOpRhs":
            assert node[2][1][0] == 'IdentifierZipAddon'
            left = (left,) + node[2][1][2::2]
            assert node[2][2][0] == 'HAS'
            if len(node[2]) == 4:
                assert node[2][3][0] == 'ValueZip'
                op = "HAS_ZIP"
                inop = None
                inops = []
                rights = []
                for x in node[2][3][1:]:
                    if x[0] == 'Operator':
                        assert inop is None
                        inop = x[1]
                    elif x[0] == 'Value':
                        rights += [_fix_const(x[1])]
                        inops += ['=' if inop is None else inop]
                        inop = None
                pos[arg] = (op, tuple(inops), left, tuple(rights))
            elif len(node[2]) == 5:
                assert node[2][3][0] in ['ONLY', 'ALL', 'EXACTLY', 'ANY']
                assert node[2][4][0] == 'ValueZipList'
                op = "HAS_ZIP_" + node[2][3][0]
                zip_inops: list[Any] = []
                zip_rights: list[Any] = []
                for y in node[2][4][1::2]:
                    inop = None
                    zip_inops += [[]]
                    zip_rights += [[]]
                    for x in y[1:]:
                        if x[0] == 'Operator':
                            assert inop is None
                            inop = x[1]
                        elif x[0] == 'Value':
                            zip_rights[-1] += [_fix_const(x[1])]
                            zip_inops[-1] += ['=' if inop is None else inop]
                            inop = None
                    zip_inops[-1] = tuple(zip_inops[-1])
                    zip_rights[-1] = tuple(zip_rights[-1])
                pos[arg] = (op, tuple(zip_inops), left, tuple(zip_rights))
            else:
                raise ParserInternalError(
                    "Filter simplify on invalid ast, unexpected number of components in set op: " + str(node[2])
                )
            arg = None
        elif node[2][0] == "LengthOpRhs":
            assert node[2][1][0] == 'LENGTH'
            assert node[2][2][0] == 'Value' or (node[2][2][0] == 'Operator' and node[2][3][0] == 'Value')
            if node[2][2][0] == 'Value':
                right = _fix_const(node[2][2][1])
                op = '='
            else:
                op = node[2][2][1]
                right = _fix_const(node[2][3][1])
            pos[arg] = ("LENGTH", left, op, right)
        else:
            raise ParserInternalError("Filter simplify on invalid ast, unrecognized comparison: " + str(node[2][0]))
    else:
        raise ParserInternalError("Filter simplify on invalid ast, unrecognized node: " + str(node[0]))

    assert arg is None or pos[arg] is not None
    return tuple(tree[0])
