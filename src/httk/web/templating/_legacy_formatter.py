import ast
import string
from html import escape
from typing import Any, Mapping, Sequence

from markupsafe import Markup


class UnquotedText(str):
    """Marker type for values that should bypass auto-escaping."""


class HttkTemplateFormatter(string.Formatter):
    def __init__(self) -> None:
        super().__init__()
        self._current_args: Sequence[Any] = ()
        self._current_kwargs: Mapping[str, Any] = {}

    def format_field(self, value: Any, spec: str) -> str:
        return self._format_field(value, spec, quote=None)

    def _format_field(self, value: Any, spec: str, quote: bool | None) -> str:
        if spec == "unquoted" or spec.startswith("unquoted:"):
            return self._format_field(value, spec[len("unquoted::") :], quote=False)
        if spec == "quote" or spec.startswith("quote:"):
            return self._format_field(value, spec[len("quote::") :], quote=True)

        if spec.startswith("repeat:"):
            template = spec.partition("::")[-1]
            new_kwargs: dict[str, Any] = dict(self._current_kwargs)
            if "item" in new_kwargs:
                prior_items = new_kwargs.get("items")
                if isinstance(prior_items, list):
                    new_kwargs["items"] = [new_kwargs["item"]] + prior_items
                else:
                    new_kwargs["items"] = [new_kwargs["item"]]
            if "index" in new_kwargs:
                prior_indices = new_kwargs.get("indices")
                if isinstance(prior_indices, list):
                    new_kwargs["indices"] = [new_kwargs["index"]] + prior_indices
                else:
                    new_kwargs["indices"] = [new_kwargs["index"]]
            if "index1" in new_kwargs:
                prior_indices = new_kwargs.get("indices")
                if isinstance(prior_indices, list):
                    new_kwargs["indices"] = [new_kwargs["index1"]] + prior_indices
                else:
                    new_kwargs["indices"] = [new_kwargs["index1"]]

            def update_and_return(update: dict[str, Any]) -> dict[str, Any]:
                new_kwargs.update(update)
                return new_kwargs

            if value is None:
                raise ValueError(f"HttkTemplateFormatter: asked to loop over None for spec: {spec}")
            if isinstance(value, dict):
                return "".join(
                    [
                        self.format(
                            template,
                            **(update_and_return({"item": value[key], "index": key, "index1": key})),
                        )
                        for key in value
                    ]
                )
            if not isinstance(value, Sequence):
                return ""
            return "".join(
                [
                    self.format(
                        template,
                        **(update_and_return({"item": value[i], "index": i, "index1": i + 1})),
                    )
                    for i in range(len(value))
                ]
            )

        if spec == "call" or spec.startswith("call:"):
            callargs, _sep, newspec = spec.partition("::")
            callargs_list = callargs.split(":")
            parsed_callargs: list[Any] = []
            for arg in callargs_list:
                if arg.startswith("{") and arg.endswith("}"):
                    parsed_callargs.append(self.get_field(arg[1:-1], self._current_args, self._current_kwargs)[0])
                else:
                    parsed_callargs.append(arg)

            if hasattr(value, "__repr__") and ("of float object" in repr(value) or "of int object" in repr(value)):
                for callarg_idx in range(1, len(parsed_callargs)):
                    try:
                        if parsed_callargs[callarg_idx] == "nan":
                            parsed_callargs[callarg_idx] = float("nan")
                        else:
                            parsed_callargs[callarg_idx] = ast.literal_eval(str(parsed_callargs[callarg_idx]))
                    except (ValueError, SyntaxError):
                        pass

            if not callable(value):
                raise TypeError(f"Templateengine_httk: tried to call non-callable value: {value!r}")
            result = value(*parsed_callargs[1:])
            return self._format_field(result, newspec, quote=quote)

        if spec.startswith("getitem:") or spec.startswith("getattr:"):
            x, _dummy, newspec = spec.partition(":")[2].partition("::")
            call_func: str | None = None
            if ":call" in x:
                call_func = x.partition(".")[-1].split(":")[0]
                x = x.partition(".")[0]
            idx: Any
            if x.startswith("{") and x.endswith("}"):
                idx = self.get_field(x[1:-1], self._current_args, self._current_kwargs)[0]
            else:
                idx = x
            try:
                val = value[idx] if spec.startswith("getitem:") else getattr(value, str(idx))
            except (TypeError, KeyError, AttributeError, IndexError):
                try:
                    int_idx = int(str(idx))
                    val = value[int_idx] if spec.startswith("getitem:") else ""
                except (TypeError, ValueError, KeyError, IndexError):
                    return ""

            if newspec == "" and call_func is None:
                return str(val)

            if call_func is not None:
                val = getattr(val, call_func)
                newspec = "call" + spec.partition(":call")[-1]
            return self._format_field(val, newspec, quote=quote)

        if (
            spec.startswith("if:")
            or spec.startswith("if-not:")
            or spec.startswith("if-set:")
            or spec.startswith("if-unset:")
        ):
            outcome = (
                (spec.startswith("if:") and bool(value))
                or (spec.startswith("if-not:") and not bool(value))
                or (spec.startswith("if-set:") and value is not None)
                or (spec.startswith("if-unset:") and value is None)
            )

            if "::else::" in spec:
                if not outcome:
                    template = spec.partition("::else::")[-1]
                else:
                    template = spec.partition("::else::")[0].partition("::")[-1]
            else:
                if not outcome:
                    return ""
                template = spec.partition("::")[-1]
            return self.format(template, **dict(self._current_kwargs))

        if value is None:
            return ""

        output = super().format_field(value, spec)
        if quote is None:
            quote = not isinstance(value, (UnquotedText, Markup))
        if quote:
            output = escape(output, quote=True).replace("'", "&apos;")
        return output

    def get_field(
        self,
        field_name: str,
        args: Sequence[Any],
        kwargs: Mapping[str, Any],
    ) -> tuple[Any, Any]:
        try:
            return super().get_field(field_name, args, kwargs)
        except (KeyError, AttributeError):
            return None, field_name

    def vformat(self, format_string: str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> str:
        self._current_args = args
        self._current_kwargs = kwargs
        return super().vformat(format_string, args, kwargs)
