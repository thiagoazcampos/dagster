import logging
import re
import textwrap
from itertools import groupby
from typing import TYPE_CHECKING, Any, Sequence

from docutils import nodes, writers
from docutils.nodes import Element
from docutils.utils import column_width

from sphinx import addnodes
from sphinx.locale import admonitionlabels
from sphinx.util.docutils import SphinxTranslator

if TYPE_CHECKING:
    from ..builders.mdx import MdxBuilder

logger = logging.getLogger(__name__)
STDINDENT = 4


def my_wrap(text: str, width: int = 120, **kwargs: Any) -> list[str]:
    w = TextWrapper(width=width, **kwargs)
    return w.wrap(text)


class TextWrapper(textwrap.TextWrapper):
    """Custom subclass that uses a different word separator regex."""

    wordsep_re = re.compile(
        r"(\s+|"  # any whitespace
        r"(?<=\s)(?::[a-z-]+:)?`\S+|"  # interpreted text start
        r"[^\s\w]*\w+[a-zA-Z]-(?=\w+[a-zA-Z])|"  # hyphenated words
        r"(?<=[\w\!\"\'\&\.\,\?])-{2,}(?=\w))"
    )  # em-dash

    def _wrap_chunks(self, chunks: list[str]) -> list[str]:
        """The original _wrap_chunks uses len() to calculate width.

        This method respects wide/fullwidth characters for width adjustment.
        """
        lines: list[str] = []
        if self.width <= 0:
            raise ValueError("invalid width %r (must be > 0)" % self.width)

        chunks.reverse()

        while chunks:
            cur_line = []
            cur_len = 0

            if lines:
                indent = self.subsequent_indent
            else:
                indent = self.initial_indent

            width = self.width - column_width(indent)

            if self.drop_whitespace and chunks[-1].strip() == "" and lines:
                del chunks[-1]

            while chunks:
                line = column_width(chunks[-1])

                if cur_len + line <= width:
                    cur_line.append(chunks.pop())
                    cur_len += line

                else:
                    break

            if chunks and column_width(chunks[-1]) > width:
                self._handle_long_word(chunks, cur_line, cur_len, width)

            if self.drop_whitespace and cur_line and cur_line[-1].strip() == "":
                del cur_line[-1]

            if cur_line:
                lines.append(indent + "".join(cur_line))

        return lines

    def _break_word(self, word: str, space_left: int) -> tuple[str, str]:
        """Break line by unicode width instead of len(word)."""
        total = 0
        for i, c in enumerate(word):
            total += column_width(c)
            if total > space_left:
                return word[: i - 1], word[i - 1 :]
        return word, ""

    def _split(self, text: str) -> list[str]:
        """Override original method that only split by 'wordsep_re'.

        This '_split' splits wide-characters into chunks by one character.
        """

        def split(t: str) -> list[str]:
            return super(TextWrapper, self)._split(t)

        chunks: list[str] = []
        for chunk in split(text):
            for w, g in groupby(chunk, column_width):
                if w == 1:
                    chunks.extend(split("".join(g)))
                else:
                    chunks.extend(list(g))
        return chunks

    def _handle_long_word(
        self, reversed_chunks: list[str], cur_line: list[str], cur_len: int, width: int
    ) -> None:
        """Override original method for using self._break_word() instead of slice."""
        space_left = max(width - cur_len, 1)
        if self.break_long_words:
            line, rest = self._break_word(reversed_chunks[-1], space_left)
            cur_line.append(line)
            reversed_chunks[-1] = rest

        elif not cur_line:
            cur_line.append(reversed_chunks.pop())


class MdxWriter(writers.Writer):
    supported = ("mdx",)
    settings_spec = ("No options here.", "", ())
    settings_defaults = {}
    output: str

    def __init__(self, builder: "MdxBuilder"):
        super().__init__()
        self.builder = builder

    def translate(self):
        self.visitor = MdxTranslator(self.document, self.builder)
        self.document.walkabout(self.visitor)
        self.output = self.visitor.body


class MdxTranslator(SphinxTranslator):
    def __init__(self, document: nodes.document, builder: "MdxBuilder") -> None:
        super().__init__(document, builder)
        self.sectionlevel = 0
        self.nl = "\n"
        self.messages: list[str] = []
        self._warned: set[str] = set()
        self.states: list[list[tuple[int, str | list[str]]]] = [[]]
        self.stateindent = [0]
        self.context: list[str] = []
        self.list_counter: list[int] = []
        self.in_literal = False
        self.desc_count = 0

        self.max_line_width = self.config.max_line_width or 120

    ############################################################
    # Utility and State Methods
    ############################################################
    def add_text(self, text: str) -> None:
        self.states[-1].append((-1, text))

    def new_state(self, indent: int = STDINDENT) -> None:
        self.states.append([])
        self.stateindent.append(indent)

    def log_visit(self, node: Element | str) -> None:
        """Utility to log the visit to a node."""
        if isinstance(node, Element):
            node_type = node.__class__.__name__
        else:
            node_type = node
        self.add_text(f"---------visit: {node_type}")

    def log_depart(self, node: Element | str) -> None:
        if isinstance(node, Element):
            node_type = node.__class__.__name__
        else:
            node_type = node
        self.add_text(f"---------depart: {node_type}")

    def end_state(
        self,
        wrap: bool = True,
        end: Sequence[str] | None = ("",),
        first: str | None = None,
    ) -> None:
        content = self.states.pop()
        maxindent = sum(self.stateindent)
        indent = self.stateindent.pop()
        result: list[tuple[int, list[str]]] = []
        toformat: list[str] = []

        def do_format() -> None:
            if not toformat:
                return
            if wrap:
                res = my_wrap("".join(toformat), width=self.max_line_width - maxindent)
            else:
                res = "".join(toformat).splitlines()
            if end:
                res += end
            result.append((indent, res))

        for itemindent, item in content:
            if itemindent == -1:
                toformat.append(item)  # type: ignore[arg-type]
            else:
                do_format()
                result.append((indent + itemindent, item))  # type: ignore[arg-type]
                toformat = []
        do_format()
        if first is not None and result:
            # insert prefix into first line (ex. *, [1], See also, etc.)
            newindent = result[0][0] - indent
            if result[0][1] == [""]:
                result.insert(0, (newindent, [first]))
            else:
                text = first + result[0][1].pop(0)
                result.insert(0, (newindent, [text]))

        self.states[-1].extend(result)

    def unknown_visit(self, node: Element) -> None:
        node_type = node.__class__.__name__
        if node_type not in self._warned:
            super().unknown_visit(node)
            self._warned.add(node_type)
        raise nodes.SkipNode

    def visit_Text(self, node: nodes.Text) -> None:
        if isinstance(node.parent, nodes.reference):
            return
        if self.in_literal:
            # Escape < characters in literal blocks
            content = node.astext().replace("<", "\\<")
        else:
            content = node.astext()
        self.add_text(content)

    def depart_Text(self, node: Element) -> None:
        pass

    def visit_document(self, node: Element) -> None:
        self.new_state(0)

    def depart_document(self, node: Element) -> None:
        self.end_state()
        self.body = self.nl.join(
            line and (" " * indent + line) for indent, lines in self.states[0] for line in lines
        )
        if self.messages:
            logger.info("---MDX Translator messages---")
            for msg in self.messages:
                logger.info(msg)
            logger.info("---End MDX Translator messages---")

    def visit_section(self, node: Element) -> None:
        self.sectionlevel += 1

    def depart_section(self, node: Element) -> None:
        self.sectionlevel -= 1

    def visit_topic(self, node: Element) -> None:
        self.new_state(0)

    def depart_topic(self, node: Element) -> None:
        self.end_state(wrap=False)

    visit_sidebar = visit_topic
    depart_sidebar = depart_topic

    def visit_rubric(self, node: Element) -> None:
        self.new_state(0)

    def depart_rubric(self, node: Element) -> None:
        self.add_text(":")
        self.end_state()

    def visit_compound(self, node: Element) -> None:
        pass

    def depart_compound(self, node: Element) -> None:
        pass

    def visit_glossary(self, node: Element) -> None:
        pass

    def depart_glossary(self, node: Element) -> None:
        pass

    def visit_title(self, node: Element) -> None:
        if isinstance(node.parent, nodes.Admonition):
            self.add_text(node.astext() + ": ")
            raise nodes.SkipNode
        self.new_state(0)

    def depart_title(self, node: Element) -> None:
        prefix = "#" * (self.sectionlevel) + " "
        self.end_state(first=prefix)

    def visit_subtitle(self, node: Element) -> None:
        pass

    def depart_subtitle(self, node: Element) -> None:
        pass

    def visit_attribution(self, node: Element) -> None:
        pass

    def depart_attribution(self, node: Element) -> None:
        pass

    #############################################################
    # Domain-specific object descriptions
    #############################################################

    # Top-level nodes
    #################

    # desc contains 1* desc_signature and a desc_content
    # desc_signature default single line signature
    # desc_signature_line node for line in multi-line signature
    # desc_content last child node, object description
    # desc_inline sig fragment in inline text

    def visit_desc(self, node: Element) -> None:
        self.desc_count += 1
        self.new_state(0)
        self.add_text("<dl>")

    def depart_desc(self, node: Element) -> None:
        self.add_text("</dl>")
        self.end_state(wrap=False, end=None)
        self.desc_count -= 1

    def visit_desc_signature(self, node: Element) -> None:
        self.new_state()
        self.add_text("<dt>")

    def depart_desc_signature(self, node: Element) -> None:
        self.add_text("</dt>")
        self.end_state(wrap=False, end=None)

    def visit_desc_signature_line(self, node: Element) -> None:
        pass

    def depart_desc_signature_line(self, node: Element) -> None:
        pass

    def visit_desc_content(self, node: Element) -> None:
        self.new_state()
        self.add_text("<dd>")

    def depart_desc_content(self, node: Element) -> None:
        self.add_text("</dd>")
        self.end_state(wrap=False, end=None)

    def visit_desc_inline(self, node: Element) -> None:
        self.add_text("<span>")

    def depart_desc_inline(self, node: Element) -> None:
        self.add_text("</span>")

    def visit_desc_sig_space(self, node: Element) -> None:
        pass

    def depart_desc_sig_space(self, node: Element) -> None:
        pass

    # High-level structure in signaturs
    #################

    # desc_name: main object name, e.g. MyModule.MyClass, the main name is MyClass.
    # desc_addname: additional name, e.g. MyModle.MyClass, the additional name is MyModule
    # desc_type: node for return types
    # desc_returns: node for return types
    # desc_parameterlist: node for parameter list
    # desc_parameter: node for a single parameter
    # desc_optional: node for optional parts of the param list
    # desc_annotation: node for signature anootations

    def visit_desc_name(self, node: Element) -> None:
        pass

    def depart_desc_name(self, node: Element) -> None:
        pass

    def visit_desc_addname(self, node: Element) -> None:
        pass

    def depart_desc_addname(self, node: Element) -> None:
        pass

    def visit_desc_type(self, node: Element) -> None:
        pass

    def depart_desc_type(self, node: Element) -> None:
        pass

    def visit_desc_returns(self, node: Element) -> None:
        self.add_text(" -> ")

    def depart_desc_returns(self, node: Element) -> None:
        pass

    def visit_desc_parameterlist(self, node: Element) -> None:
        raise nodes.SkipNode

    def depart_desc_parameterlist(self, node: Element) -> None:
        pass

    def visit_desc_type_parameterlist(self, node: Element) -> None:
        pass

    def depart_desc_type_parameterlist(self, node: Element) -> None:
        pass

    def visit_desc_parameter(self, node: Element) -> None:
        pass

    def depart_desc_parameter(self, node: Element) -> None:
        pass

    def visit_desc_type_parameter(self, node: Element) -> None:
        pass

    def depart_desc_type_parameter(self, node: Element) -> None:
        pass

    def visit_desc_optional(self, node: Element) -> None:
        pass

    def depart_desc_optional(self, node: Element) -> None:
        pass

    def visit_desc_annotation(self, node: Element) -> None:
        pass

    def depart_desc_annotation(self, node: Element) -> None:
        pass

    # Docutils nodes
    ###############

    def visit_paragraph(self, node: Element) -> None:
        if not (
            isinstance(
                node.parent,
                (nodes.list_item, nodes.entry, addnodes.desc_content, nodes.field_body),
            )
            and (len(node.parent) == 1)
        ):
            self.new_state(0)

    def depart_paragraph(self, node: Element) -> None:
        if not (
            isinstance(
                node.parent,
                (nodes.list_item, nodes.entry, addnodes.desc_content, nodes.field_body),
            )
            and (len(node.parent) == 1)
        ):
            self.end_state(wrap=False)

    def visit_reference(self, node: Element) -> None:
        ref_text = node.astext()
        if "refuri" in node:
            self.reference_uri = node["refuri"]
        elif "refid" in node:
            self.reference_uri = f"#{node['refid']}"
        else:
            self.messages.append('References must have "refuri" or "refid" attribute.')
            raise nodes.SkipNode
        self.add_text(f"[{ref_text}]({self.reference_uri})")

    def depart_reference(self, node: Element) -> None:
        self.reference_uri = ""

    def visit_title_reference(self, node: Element) -> None:
        self.add_text("xxx")

    def depart_title_reference(self, node: Element) -> None:
        self.add_text("xxx")

    def visit_image(self, node: Element) -> None:
        self.add_text(f"![{node.get('alt', '')}]({node['uri']})")

    def depart_image(self, node: Element) -> None:
        pass

    def visit_target(self, node: Element) -> None:
        pass

    def depart_target(self, node: Element) -> None:
        pass

    def visit_comment(self, node: Element) -> None:
        raise nodes.SkipNode

    def visit_admonition(self, node: Element) -> None:
        self.new_state(0)

    def depart_admonition(self, node: Element) -> None:
        self.end_state()

    def _visit_admonition(self, node: Element) -> None:
        self.new_state(2)

    def _depart_admonition(self, node: Element) -> None:
        label = admonitionlabels[node.tagname]
        self.stateindent[-1] += len(label)
        self.end_state(first=label + ": ")

    visit_attention = _visit_admonition
    depart_attention = _depart_admonition
    visit_caution = _visit_admonition
    depart_caution = _depart_admonition
    visit_danger = _visit_admonition
    depart_danger = _depart_admonition
    visit_error = _visit_admonition
    depart_error = _depart_admonition
    visit_hint = _visit_admonition
    depart_hint = _depart_admonition
    visit_important = _visit_admonition
    depart_important = _depart_admonition
    visit_note = _visit_admonition
    depart_note = _depart_admonition
    visit_tip = _visit_admonition
    depart_tip = _depart_admonition
    visit_warning = _visit_admonition
    depart_warning = _depart_admonition
    visit_seealso = _visit_admonition
    depart_seealso = _depart_admonition

    ###################################################
    # Lists
    ##################################################
    def visit_definition(self, node: Element) -> None:
        self.new_state()

    def depart_definition(self, node: Element) -> None:
        self.end_state()

    def visit_definition_list(self, node: Element) -> None:
        self.list_counter.append(-2)

    def depart_definition_list(self, node: Element) -> None:
        self.list_counter.pop()

    def visit_definition_list_item(self, node: Element) -> None:
        self._classifier_count_in_li = len(list(node.findall(nodes.classifier)))

    def depart_definition_list_item(self, node: Element) -> None:
        pass

    def visit_list_item(self, node: Element) -> None:
        if self.list_counter[-1] == -1:
            self.new_state(2)
            # bullet list
        elif self.list_counter[-1] == -2:
            # definition list
            pass
        else:
            # enumerated list
            self.list_counter[-1] += 1
            self.new_state(len(str(self.list_counter[-1])) + 2)

    def depart_list_item(self, node: Element) -> None:
        if self.list_counter[-1] == -1:
            self.end_state(first="- ", wrap=False)
            self.states[-1].pop()
        elif self.list_counter[-1] == -2:
            pass
        else:
            self.end_state(first=f"{self.list_counter[-1]}. ", wrap=False, end=None)

    def visit_bullet_list(self, node: Element) -> None:
        self.list_counter.append(-1)
        self.new_state(2)

    def depart_bullet_list(self, node: Element) -> None:
        self.list_counter.pop()
        self.add_text(self.nl)
        self.end_state(wrap=False)

    def visit_enumerated_list(self, node: Element) -> None:
        self.list_counter.append(node.get("start", 1) - 1)

    def depart_enumerated_list(self, node: Element) -> None:
        self.list_counter.pop()

    def visit_term(self, node: Element) -> None:
        self.new_state(0)

    def depart_term(self, node: Element) -> None:
        if not self._classifier_count_in_li:
            self.end_state(end=None)

    def visit_classifier(self, node: Element) -> None:
        self.add_text(" : ")

    def depart_classifier(self, node: Element) -> None:
        self._classifier_count_in_li -= 1
        if not self._classifier_count_in_li:
            self.end_state(end=None)

    def visit_field_list(self, node: Element) -> None:
        self.new_state(0)

    def depart_field_list(self, node: Element) -> None:
        self.end_state(wrap=False, end=None)

    def visit_field(self, node: Element) -> None:
        pass

    def depart_field(self, node: Element) -> None:
        pass

    def visit_field_name(self, node: Element) -> None:
        pass

    def depart_field_name(self, node: Element) -> None:
        self.add_text(": ")

    def visit_field_body(self, node: Element) -> None:
        pass

    def depart_field_body(self, node: Element) -> None:
        pass

    # Inline elements
    #################

    def visit_emphasis(self, node: Element) -> None:
        self.add_text("<em>")

    def depart_emphasis(self, node: Element) -> None:
        self.add_text("</em>")

    def visit_literal_emphasis(self, node: Element) -> None:
        return self.visit_emphasis(node)

    def depart_literal_emphasis(self, node: Element) -> None:
        return self.depart_emphasis(node)

    def visit_strong(self, node: Element) -> None:
        self.add_text("<strong>")

    def depart_strong(self, node: Element) -> None:
        self.add_text("</strong>")

    def visit_literal_strong(self, node: Element) -> None:
        return self.visit_strong(node)

    def depart_literal_strong(self, node: Element) -> None:
        return self.depart_strong(node)

    def visit_literal(self, node: Element) -> None:
        self.in_literal = True
        self.add_text("`")

    def depart_literal(self, node: Element) -> None:
        self.in_literal = False
        self.add_text("`")

    def visit_literal_block(self, node: Element) -> None:
        self.in_literal = True
        lang = node.get("language", "default")
        self.new_state()
        self.add_text(f"```{lang}\n")

    def depart_literal_block(self, node: Element) -> None:
        self.in_literal = False
        self.end_state(wrap=False, end=["```"])

    def visit_inline(self, node: Element) -> None:
        self.in_literal = True
        self.add_text("`")

    def depart_inline(self, node: Element) -> None:
        self.in_literal = False
        self.add_text("`")

    def visit_problematic(self, node: Element) -> None:
        self.add_text(f"```\n{node.astext()}\n```")
        raise nodes.SkipNode

    # Misc. skipped nodes
    #####################o

    def visit_index(self, node: Element) -> None:
        raise nodes.SkipNode

    def visit_toctree(self, node: Element) -> None:
        raise nodes.SkipNode

    ################################################################################
    # tables
    ################################################################################
    # table
    #   tgroup [cols=x]
    #     colspec
    #     thead
    #       row
    #         entry
    #           paragraph (optional)
    #     tbody
    #       row
    #         entry
    #           paragraph (optional)
    ###############################################################################
    def visit_table(self, node: Element) -> None:
        self.new_state(0)
        self.table_header = []
        self.table_body = []
        self.current_row = []
        self.in_table_header = False

    def depart_table(self, node: Element) -> None:
        if self.table_header:
            self.add_text("| " + " | ".join(self.table_header) + " |" + self.nl)
            separators = []
            for i, width in enumerate(self.colwidths):
                align = self.colaligns[i]
                if align == "left":
                    separators.append(":" + "-" * (width - 1))
                elif align == "right":
                    separators.append("-" * (width - 1) + ":")
                elif align == "center":
                    separators.append(":" + "-" * (width - 2) + ":")
                else:
                    separators.append("-" * width)
            self.add_text("| " + " | ".join(separators) + " |" + self.nl)

        for row in self.table_body:
            self.add_text("| " + " | ".join(row) + " |" + self.nl)

        self.add_text(self.nl)
        self.end_state(wrap=False)

    def visit_thead(self, node: Element) -> None:
        self.in_table_header = True

    def depart_thead(self, node: Element) -> None:
        self.in_table_header = False

    def visit_tbody(self, node: Element) -> None:
        pass

    def depart_tbody(self, node: Element) -> None:
        pass

    def visit_tgroup(self, node: Element) -> None:
        self.colwidths = []
        self.colaligns = []

    def depart_tgroup(self, node: Element) -> None:
        pass

    def visit_colspec(self, node: Element) -> None:
        self.colwidths.append(node["colwidth"])
        self.colaligns.append(node.get("align", "left"))

    def depart_colspec(self, node: Element) -> None:
        pass

    def visit_row(self, node: Element) -> None:
        self.current_row = []

    def depart_row(self, node: Element) -> None:
        if self.in_table_header:
            self.table_header = self.current_row
        else:
            self.table_body.append(self.current_row)

    def visit_entry(self, node: Element) -> None:
        self.new_state(0)

    def depart_entry(self, node: Element) -> None:
        text = self.nl.join(
            content.strip() if isinstance(content, str) else content[0].strip()
            for _, content in self.states.pop()
            if content
        )
        self.current_row.append(text.replace("\n", ""))
        self.stateindent.pop()

    # Dagster specific nodes
    ###########################################################################
    # TODO: Move these out of this module and extract out docusaurus
    # style admonitions
    def visit_flag(self, node: Element) -> None:
        flag_type = node.attributes["flag_type"]
        message = node.attributes["message"].replace(":::", "")
        set_flag = "info"
        if flag_type == "experimental":
            set_flag = "danger"
        if flag_type == "deprecated":
            set_flag = "warning"

        self.new_state()
        self.add_text(f":::{set_flag}[{flag_type}]\n")
        self.add_text(f"{message}\n")

    def depart_flag(self, node: Element) -> None:
        self.add_text("\n:::\n")
        self.end_state(wrap=False)