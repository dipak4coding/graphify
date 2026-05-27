# extract.py — AST Extraction (How Code Is Read)

Tags: #module #layer1 #ast #tree-sitter
Links: [[00_INDEX]] | [[03_detect]] | [[05_extract_semantic]] | [[07_nodes_and_edges_deep_dive]]
File: `graphify/extract.py`

---

## What Is an AST?

When you write Python code like:

```python
class Dog:
    def bark(self):
        print("woof")
```

The Python interpreter first converts this text into an **Abstract Syntax Tree (AST)** — a tree structure where each node represents a language construct:

```
module
└── class_definition
    ├── name: "Dog"
    └── body
        └── function_definition
            ├── name: "bark"
            └── body
                └── call
                    ├── function: print
                    └── arguments: "woof"
```

Graphify uses **tree-sitter**, a fast parsing library, to build this tree. Then it **walks the tree** looking for classes, functions, imports, and function calls — and converts them into graph nodes and edges.

This is **100% deterministic and free** — no AI needed. Tree-sitter is a C library with Python bindings.

---

## LanguageConfig — The Grammar Adapter

Every programming language has different grammar. `class` in Python is `class_definition`; in Java it's `class_declaration`; in Ruby it's just `class`.

`LanguageConfig` is a **dataclass** (a Python class that's just a container for data, with no methods) that describes one language's grammar to the generic extractor:

```python
@dataclass
class LanguageConfig:
    ts_module: str              # Python package name: "tree_sitter_python"
    ts_language_fn: str         # Function to call to get the language: "language"
    
    class_types: frozenset      # AST node types that are class definitions
    function_types: frozenset   # AST node types that are function definitions
    import_types: frozenset     # AST node types that are import statements
    call_types: frozenset       # AST node types that are function calls
    
    name_field: str             # The field name inside a node that holds its name
    name_fallback_child_types   # If name_field doesn't work, try these child types
    
    body_field: str             # The field name for the body block
    body_fallback_child_types   # If body_field doesn't work, try these child types
    
    call_function_field         # Field on a call node that gives the callee
    call_accessor_node_types    # Types for method calls (obj.method())
    call_accessor_field         # Field on accessor node for the method name
    
    function_boundary_types     # Stop looking for calls when you enter these nodes
    import_handler              # Language-specific import parsing function
    resolve_function_name_fn    # For C/C++: unwrap complex declarators
    function_label_parens: bool # If True, functions get "name()" label
    extra_walk_fn               # Language-specific hook for unusual constructs
```

**Example — Python:**
```python
_PYTHON_CONFIG = LanguageConfig(
    ts_module="tree_sitter_python",
    class_types=frozenset({"class_definition"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"import_statement", "import_from_statement"}),
    call_types=frozenset({"call"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"attribute"}),
    call_accessor_field="attribute",
    function_boundary_types=frozenset({"function_definition"}),
    import_handler=_import_python,
)
```

**Example — Java (note different field names):**
```python
_JAVA_CONFIG = LanguageConfig(
    ts_module="tree_sitter_java",
    class_types=frozenset({"class_declaration", "interface_declaration"}),
    function_types=frozenset({"method_declaration", "constructor_declaration"}),
    import_types=frozenset({"import_declaration"}),
    call_types=frozenset({"method_invocation"}),
    call_function_field="name",   # ← different from Python's "function"
    ...
)
```

This design means you add one `LanguageConfig` to support a new language — the generic extractor does all the work.

---

## The Generic Extractor — How It Works

The main function is `extract_file(path, config)`. Let's trace through what happens when it processes a Python file.

### 1. Load and parse

```python
import importlib
ts_mod = importlib.import_module(config.ts_module)  # tree_sitter_python
language_fn = getattr(ts_mod, config.ts_language_fn)
language = language_fn()

from tree_sitter import Language, Parser
parser = Parser(language)
source = path.read_bytes()
tree = parser.parse(source)
```

- `importlib.import_module()` dynamically loads the language grammar package (e.g. `tree_sitter_python`)
- `parser.parse(source)` returns the full AST tree
- `source` is kept as `bytes` (not decoded to string yet) because tree-sitter works with byte positions

### 2. Create the file node

```python
stem = path.stem          # e.g. "client" from "client.py"
str_path = str(path)
file_nid = _make_id(stem) # e.g. "client"

nodes.append({
    "id": file_nid,
    "label": path.name,   # "client.py"
    "file_type": "code",
    "source_file": str_path,
    "source_location": "L1",
})
```

Every code file gets a **file node** at line 1. This node becomes the hub that all classes and functions inside it connect to via `contains` edges.

### 3. Walk the tree

```python
def walk(node, parent_class_nid=None):
    if node.type in config.class_types:
        handle_class(node, parent_class_nid)
    elif node.type in config.function_types:
        handle_function(node, parent_class_nid)
    elif node.type in config.import_types:
        handle_import(node)
    else:
        for child in node.children:
            walk(child, parent_class_nid)
```

`walk()` is a **recursive function** that visits every node in the AST. At each node, it checks if the node type is something interesting. If it is, it handles it. If not, it recurses into the children.

`parent_class_nid` is passed down so that when we find a method inside a class, we know which class to attach it to.

### 4. Handle a class

```python
def handle_class(node, parent_class_nid):
    name = _resolve_name(node, source, config)  # read "Dog" from name field
    if not name:
        return
    
    line = node.start_point[0] + 1  # tree-sitter uses 0-based lines
    class_nid = _make_id(stem, name)  # "client_dog"
    
    if class_nid not in seen_ids:  # deduplication within this file
        seen_ids.add(class_nid)
        nodes.append({
            "id": class_nid,
            "label": name,
            "file_type": "code",
            "source_file": str_path,
            "source_location": f"L{line}",
        })
        # Edge: file → class
        edges.append({
            "source": file_nid,
            "target": class_nid,
            "relation": "contains",
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
            ...
        })
    
    # Now walk the class body for methods
    body = _find_body(node, config)
    if body:
        for child in body.children:
            walk(child, parent_class_nid=class_nid)
```

Key points:
- `node.start_point[0] + 1` — tree-sitter counts lines from 0, so we add 1 to match what editors show
- `seen_ids` is a set that tracks which node IDs we've emitted in this file. If the same class name appears twice (e.g. a class redefinition), only the first occurrence is recorded.
- `_find_body()` finds the class body node using `config.body_field` (or fallback child types for languages that don't have a named body field)

### 5. Handle a function

Same as class, but also:

```python
# Collect function body for call extraction later
body = _find_body(node, config)
if body:
    function_bodies.append((func_nid, body))
```

Function bodies are saved in a list. After all nodes are created, we walk these bodies looking for function calls.

### 6. Handle imports (language-specific)

Each language has a different import syntax. Rather than one giant if-else, graphify delegates to a **per-language import handler**:

```python
if config.import_handler:
    config.import_handler(node, source, file_nid, stem, edges, str_path)
```

**Python import handler:**
```python
def _import_python(node, source, file_nid, stem, edges, str_path):
    t = node.type
    if t == "import_statement":
        # "import os" → edge from file to "os"
        for child in node.children:
            if child.type in ("dotted_name", "aliased_import"):
                raw = _read_text(child, source)
                module_name = raw.split(" as ")[0].strip().lstrip(".")
                # "import os.path as p" → module_name = "os.path"
                tgt_nid = _make_id(module_name)
                edges.append({
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                    "confidence_score": 1.0,
                    "weight": 1.0,
                })
    elif t == "import_from_statement":
        # "from graphify.detect import detect" → edge from file to "graphify.detect"
        module_node = node.child_by_field_name("module_name")
        if module_node:
            raw = _read_text(module_node, source).lstrip(".")
            # lstrip(".") handles relative imports: "from .detect import ..."
            ...
```

Notice that import **targets** may not exist as nodes in the graph (e.g., `requests` is an external library with no code in the corpus). These **dangling edges** are skipped in `build.py` — they're not errors, just expected gaps.

### 7. Extract function calls

After all class and function nodes are created, we go back to the saved function bodies and extract calls:

```python
def walk_calls(node, caller_nid):
    if node.type in config.call_types:
        # Get the callee name
        callee_name = _extract_call_name(node, source, config)
        if callee_name:
            tgt_nid = _make_id(stem, callee_name)
            # Only add call edge if target node exists in this file
            if tgt_nid in seen_ids:
                edges.append({
                    "source": caller_nid,
                    "target": tgt_nid,
                    "relation": "calls",
                    ...
                })
    
    # Stop recursing when you hit another function boundary
    if node.type in config.function_boundary_types:
        return
    
    for child in node.children:
        walk_calls(child, caller_nid)
```

**Why stop at function boundaries?** If `foo()` calls `bar()`, and `bar()` calls `baz()`, we want `foo → bar` and `bar → baz`, not `foo → bar → baz`. By stopping recursion when we enter a nested function definition, we get one edge per direct call.

---

## _read_text() — Getting the Text from a Tree-Sitter Node

```python
def _read_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
```

Tree-sitter nodes store **byte positions** (`start_byte`, `end_byte`), not character positions. This handles multi-byte Unicode characters correctly. We slice the raw `bytes` and decode to string. `errors="replace"` means invalid UTF-8 bytes become the `?` character instead of crashing.

---

## Special Cases: C and C++ Function Names

C and C++ have **declarators** — the function name is buried inside nested declarations:

```c
int (*compare)(const char *, const char *) { ... }
```

Here the function name `compare` is inside a `pointer_declarator` inside a `function_declarator`. `_get_c_func_name()` recursively unwraps these layers:

```python
def _get_c_func_name(node, source: bytes) -> str | None:
    if node.type == "identifier":
        return _read_text(node, source)         # found it!
    decl = node.child_by_field_name("declarator")
    if decl:
        return _get_c_func_name(decl, source)   # go deeper
    for child in node.children:
        if child.type == "identifier":
            return _read_text(child, source)
    return None
```

---

## Special Cases: JavaScript Arrow Functions

```javascript
const fetchData = async (url) => { ... }
```

This is a `lexical_declaration` containing a `variable_declarator` whose value is an `arrow_function`. The generic extractor only looks for `function_declaration` — it misses this pattern.

The `_js_extra_walk()` hook handles it:

```python
def _js_extra_walk(node, source, file_nid, stem, ...):
    if node.type == "lexical_declaration":
        for child in node.children:
            if child.type == "variable_declarator":
                value = child.child_by_field_name("value")
                if value and value.type == "arrow_function":
                    name_node = child.child_by_field_name("name")
                    # Create a function node for "fetchData"
                    ...
        return True  # handled — don't recurse generically
    return False
```

The `extra_walk_fn` hook in `LanguageConfig` lets you add language-specific patterns without modifying the generic extractor.

---

## The extract() Entry Point

```python
def extract(paths: list[Path]) -> dict:
    all_nodes, all_edges = [], []
    
    for path in paths:
        config = _get_config(path.suffix)
        if config is None:
            continue
        
        # Check cache first
        cached = load_cached(path)
        if cached:
            all_nodes.extend(cached["nodes"])
            all_edges.extend(cached["edges"])
            continue
        
        # No cache — extract and save
        result = extract_file(path, config)
        save_cached(path, result)
        all_nodes.extend(result["nodes"])
        all_edges.extend(result["edges"])
    
    return {"nodes": all_nodes, "edges": all_edges, "input_tokens": 0, "output_tokens": 0}
```

`_get_config(suffix)` maps file extensions to `LanguageConfig` instances. Unknown extensions return `None` and are skipped.

---

## Summary — What extract.py Produces

For a Python file `client.py` containing a class `HTTPClient` with a method `get()` that calls `requests.get()`:

**Nodes:**
```json
[
  {"id": "client",            "label": "client.py",     "source_location": "L1"},
  {"id": "client_httpclient", "label": "HTTPClient",    "source_location": "L3"},
  {"id": "client_get",        "label": "get()",         "source_location": "L5"},
  {"id": "requests",          "label": "requests",      "source_location": "L1"}
]
```

**Edges:**
```json
[
  {"source": "client",            "target": "requests",          "relation": "imports"},
  {"source": "client",            "target": "client_httpclient", "relation": "contains"},
  {"source": "client_httpclient", "target": "client_get",        "relation": "method"},
  {"source": "client_get",        "target": "requests",          "relation": "calls"}
]
```

---

*Next: [[05_extract_semantic]] — How Claude reads docs and papers*
