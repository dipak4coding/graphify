# AST Optimizer — Cleaning Up Tokens Before Parsing

Tags: #ast #optimizer #tokens
Links: [[00_INDEX]] | [[08_ast_lexer]] | [[10_ast_parser]]

---

## Purpose

The Optimizer sits between the Lexer and the Parser. It takes the raw flat token list and cleans it up so the Parser can work correctly:

```
Lexer tokens  →  [Optimizer]  →  Clean tokens  →  Parser
```

It runs a set of normalisation rules on the token list, repeating until no more changes occur.

---

## The Loop

```csharp
public Optimizer(List<object> tok)
{
    tokens = tok;
    Optimize(0x0144);  // bitmask selects which rules to apply
}

private void Optimize(short a_maske)
{
    bool modified;
    do {
        modified = false;
        // Rule 1 ... Rule 7
    } while (modified == true);
}
```

The bitmask `0x0144` = binary `0001 0100 0100`:
- Bit 2 (`0x0004`): Rule 3 — convert `-literal` to `Neg literal`
- Bit 6 (`0x0040`): Rule 7 — check functions have brackets
- Bit 8 (`0x0100`): Rule 8 — handle `defined(VAR)` → ASTdefine

Rules 1, 2, 4, 5, 6 are disabled (`0x0144` does NOT set bits 0, 1, 3, 4, 5).

---

## Rule 3 (Active) — Unary Minus `−literal` → `Neg literal`

**Problem**: After lexing, `-5` produces `[Token.Sub, intLiteral(5)]`. But `Sub` is a binary operator. For the Parser to handle unary minus correctly, it must be tagged as `Token.Neg`.

**Rule**: If `Token.Sub` appears at position 0, OR directly after another operator token, AND is followed by a literal/variable/function, replace it with `Token.Neg`.

```csharp
if ((Token)tokens[indexO] == Token.Sub)
{
    if (
          (indexO == 0 || (tokens[indexO - 1] is Token))
        &&
          (tokens[indexO + 1] is intLiteral || doubleLiteral || variable || function-Token)
       )
    {
        tokens[indexO] = Token.Neg;  // replace Sub with Neg
        modified = true;
    }
}
```

Example: `-5 + 3` becomes `[Neg, intLiteral(5), Add, intLiteral(3)]`

---

## Rule 7 (Active) — Functions Must Have Brackets

```csharp
if (Lexer.functions.ContainsValue((Token)tokens[indexO]))
{
    if (tokens[indexO + 1] is not Token.BraceLeft)
        throw new FormatException("Function ohne Klammer!");
}
```

Validates that every function token (`sqrt`, `abs`, `defined`) is immediately followed by `(`. This catches malformed expressions like `sqrt5` instead of `sqrt(5)`.

---

## Rule 8 (Active) — `defined(VAR)` → ASTdefine Node

The Lexer already sets `handleDefine=true` when it sees the `defined` keyword, which causes the next identifier to be created as `ASTdefine` instead of `variable`. However, the Optimizer adds a check for the specific pattern:

```
[Token.Defined, Token.BraceLeft, variable, Token.BraceRight]
→
[Token.Defined, Token.BraceLeft, ASTdefine, Token.BraceRight]
```

```csharp
if ((Token)tokens[indexO] == Token.Defined)
{
    if (((Token)tokens[indexO + 1] == Token.BraceLeft)
    && (tokens[indexO + 2] is variable)
    && ((Token)tokens[indexO + 3] == Token.BraceRight))
    {
        variable l_var = (variable)tokens[indexO + 2];
        ASTdefine l_def = new ASTdefine();
        l_def.Name = l_var.Ident;
        tokens[indexO + 2] = l_def;  // upgrade variable to ASTdefine
    }
}
```

The difference between `variable` and `ASTdefine`:
- `variable` → in the Calculator, its value is looked up in `D_Defines` and followed recursively
- `ASTdefine` → in the Calculator, it does `a_Defines.ContainsKey(name)` — returns bool

---

## Rules 1, 2, 4, 5, 6 (Disabled in Current Usage)

These rules exist in the code but are disabled via the `0x0144` bitmask. They were apparently written for a more general math expression evaluator context. For `#if` boolean evaluation, they are not needed:

| Rule | Bit | What it does |
|------|-----|-------------|
| 1 | 0x0001 | `--` → `+` (double negation elimination) |
| 2 | 0x0002 | Remove redundant `+` at start or after operator |
| 4 | 0x0008 | Sort chain terms (variables before literals in multiplication) |
| 5 | 0x0010 | Fold constant multiplications: `3 * 4` → `12` |
| 6 | 0x0020 | Insert `*` for implicit multiplication: `2X` → `2*X` |

These would be useful for a general algebra simplifier but are irrelevant for `#if defined(X) && Y==3` style expressions.

---

## Example Full Trace

Input expression (after lexer): `!defined(FEATURE) && (VERSION>=2)`

Lexer output:
```
[Token.NOT, Token.Defined, Token.BraceLeft, ASTdefine("FEATURE"), 
 Token.BraceRight, Token.AND, Token.BraceLeft, variable("VERSION"),
 Token.GE, intLiteral(2), Token.BraceRight]
```

(The Lexer's `handleDefine` flag already created `ASTdefine("FEATURE")` directly.)

Optimizer — Rule 3: No unary minus → no change  
Optimizer — Rule 7: `Token.Defined` followed by `Token.BraceLeft` ✓  
Optimizer — Rule 8: pattern matches `[Defined, BraceLeft, ASTdefine, BraceRight]` → already `ASTdefine`, no change  

Output: unchanged (token list already clean).

The Optimizer's main value is for expressions like `-X < -5` where unary minus needs converting.

---

*Next: [[10_ast_parser]]*
