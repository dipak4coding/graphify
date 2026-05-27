# How to Add a New Programming Language

Tags: #extension #development #how-to
Links: [[00_INDEX]] | [[04_extract_ast]] | [[20_how_to_modify_graphify]]

---

## Overview

Adding a new language to graphify requires changes in two files:
1. `detect.py` — add the file extensions
2. `extract.py` — add a `LanguageConfig` and import handler

No other files need to change for basic language support.

---

## Step 1 — Add File Extensions to detect.py

Open `graphify/detect.py` and find:

```python
CODE_EXTENSIONS = {'.py', '.ts', '.js', ... }
```

Add your language's extensions:

```python
CODE_EXTENSIONS = {'.py', '.ts', '.js', ..., '.elm', '.fs'}  # added .elm (Elm), .fs (F#)
```

That's all for `detect.py`. The new extensions will now be classified as `FileType.CODE` and collected for AST extraction.

---

## Step 2 — Install the tree-sitter Grammar

Check if a tree-sitter grammar exists for your language:
- Go to https://github.com/tree-sitter
- Search for `tree-sitter-<language>` (e.g. `tree-sitter-elm`)
- Install the Python package: `pip install tree-sitter-elm`

If no grammar exists, you can't use AST extraction for that language. Consider adding it to semantic extraction instead (the subagents can read any text file).

---

## Step 3 — Write an Import Handler

Look at the language's import syntax. Write a handler function:

```python
# Example: Elm imports look like:
# import Html exposing (div, text)
# import Browser

def _import_elm(node, source: bytes, file_nid: str, stem: str, edges: list, str_path: str) -> None:
    # node is the import AST node
    # Find the module name inside the node
    for child in node.children:
        if child.type == "upper_case_qid":  # Elm's module identifier type
            raw = _read_text(child, source)
            module_name = raw.split(".")[-1]  # take last segment: "Html" from "Html.Events"
            if module_name:
                tgt_nid = _make_id(module_name)
                edges.append({
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                    "confidence_score": 1.0,
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                })
            break
```

**How to figure out the node type names:**

Use tree-sitter's playground or write a test:
```python
import tree_sitter_elm
from tree_sitter import Language, Parser

lang = Language(tree_sitter_elm.language())
parser = Parser(lang)
code = b'import Html exposing (div)'
tree = parser.parse(code)

def print_tree(node, indent=0):
    print("  " * indent + f"{node.type}: {repr(node.text)}")
    for child in node.children:
        print_tree(child, indent + 1)

print_tree(tree.root_node)
```

This prints the full AST so you can see what node types appear in import statements.

---

## Step 4 — Create the LanguageConfig

Look at the existing configs for guidance. Here's a template:

```python
_ELM_CONFIG = LanguageConfig(
    ts_module="tree_sitter_elm",           # pip package name
    ts_language_fn="language",             # function to call: tree_sitter_elm.language()
    
    # What AST node types represent classes and functions?
    class_types=frozenset({"type_declaration", "type_alias_declaration"}),
    function_types=frozenset({"value_declaration"}),
    
    # What AST node types are imports?
    import_types=frozenset({"import_clause"}),
    
    # What AST node types are function calls?
    call_types=frozenset({"function_call_expr"}),
    
    # Field names (check the grammar's node-types.json)
    name_field="name",                     # field on class/function node that holds the name
    call_function_field="function",        # field on call node that holds the callee
    
    # If name_field doesn't work, try these child types
    name_fallback_child_types=("lower_case_identifier",),
    
    # Function body field
    body_field="body",
    
    # Accessor node for method calls: obj.method()
    call_accessor_node_types=frozenset(),  # Elm is functional, no method calls
    call_accessor_field="",
    
    # Stop looking for calls when entering another function
    function_boundary_types=frozenset({"value_declaration"}),
    
    # Use the import handler you wrote in Step 3
    import_handler=_import_elm,
)
```

---

## Step 5 — Register the Config

Find the `_LANGUAGE_CONFIGS` mapping in `extract.py` (or the `_get_config()` function):

```python
_LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    ".py": _PYTHON_CONFIG,
    ".ts": _TS_CONFIG,
    ".tsx": _TS_CONFIG,  # same grammar as TS
    ...
    ".elm": _ELM_CONFIG,  # add this line
    ".fs": _FSHARP_CONFIG,
}
```

---

## Step 6 — Write a Test

Look at `tests/test_languages.py` for examples. Add a test fixture:

1. Create `tests/fixtures/sample.elm` with a small Elm file:
```elm
module Main exposing (main)

import Html exposing (div, text)
import Browser

main = Browser.sandbox { init = 0, view = view, update = update }

view model = div [] [ text "Hello" ]

update msg model = model
```

2. Add a test:
```python
def test_elm_extraction():
    from graphify.extract import extract
    from pathlib import Path
    
    result = extract([Path("tests/fixtures/sample.elm")])
    node_labels = {n["label"] for n in result["nodes"]}
    
    assert "sample.elm" in node_labels    # file node
    assert "main" in node_labels or any("main" in l for l in node_labels)
    
    import_edges = [e for e in result["edges"] if e["relation"] == "imports"]
    assert len(import_edges) > 0          # found some imports
```

---

## Common Pitfalls

### "I get 0 nodes"

- Check that `ts_module` matches the pip package name exactly
- Check that `ts_language_fn` is the right function name (usually `"language"`, sometimes `"language_typescript"` for TS)
- Print the AST tree to verify the node type names match what you put in `class_types` and `function_types`

### "Imports are not extracted"

- Check that `import_types` contains the right AST node type names
- Print the AST for an import statement and check the node types
- Make sure `import_handler` is set in the config

### "Function calls are not found"

- Check `call_types` contains the right AST node type
- Check `call_function_field` is the right field name (use AST tree printing to verify)
- Make sure `function_boundary_types` includes function definition types so the recursion stops properly

### "ModuleNotFoundError: No module named tree_sitter_X"

The grammar package isn't installed. Run `pip install tree-sitter-<language>`.

### "TypeError: language() takes 0 positional arguments but 1 was given"

Different versions of the tree-sitter API. Set `ts_language_fn` to `"language"` (no args) or check the package's API. Some packages use `LANGUAGE` (a constant) instead of `language()` (a function).

---

## How to Know the AST Structure

Every tree-sitter grammar has a `grammar.js` file and a generated `node-types.json`. The `node-types.json` lists all node types and their fields:

```json
{
  "type": "import_clause",
  "named": true,
  "fields": {
    "module": {"multiple": false, "required": true, "types": [{"type": "upper_case_qid"}]}
  }
}
```

This tells you:
- The import node type is `"import_clause"`
- It has a field called `"module"`
- The module field contains an `"upper_case_qid"` node

So you'd use `node.child_by_field_name("module")` to get the module name.

---

*Next: [[20_how_to_modify_graphify]] — Your complete modification guide*
