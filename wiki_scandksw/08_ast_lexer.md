# AST Lexer — Tokenising `#if` Expressions

Tags: #ast #lexer #tokeniser
Links: [[00_INDEX]] | [[07_ifdef_state_machine]] | [[09_ast_optimizer]]

---

## Where the Lexer Sits

The Lexer is the first step in the `#if` expression mini-compiler pipeline:

```
Raw #if string  →  [Lexer]  →  Token list  →  Optimizer  →  Parser  →  Compiler  →  Calculator
```

It only runs for `#if` and `#elif` expressions (NOT `#ifdef`/`#ifndef` which are handled by simple dictionary lookup in `parseIfs()`).

---

## Input Examples

```c
#if CS_MODUS_DERIVAT == EN_MODUS_DERIVAT_DQ381G4
#if (VERSION_MAJOR > 3) && defined(FEATURE_NEW_CAN)
#elif KRS_VERSION != 0
#if !defined(__freeimpl__) || PLATFORM == 2
```

After `parseIfs()` strips the `#if` / `#elif` prefix, the Lexer receives just the expression:
```
CS_MODUS_DERIVAT==EN_MODUS_DERIVAT_DQ381G4
(VERSION_MAJOR>3)&&defined(FEATURE_NEW_CAN)
```

(Spaces were removed by the `Regex.Replace(a_line, @"s", "")` before calling the Lexer — see [[07_ifdef_state_machine]].)

---

## Token Types

Defined in `AST/AST.cs`:

```csharp
public enum Token
{
    BraceLeft, BraceRight,     // ( )
    Neg, NOT,                  // unary: -(negative sign), !
    Sqrt, Abs, Defined,        // functions: sqrt(), abs(), defined()
    Mul, Div, Modulo,          // * / %
    Add, Sub,                  // + -
    BitAnd, BitOr, Xor,        // & | ^
    LT, LE, GT, GE,            // < <= > >=
    EQ, NEQ,                   // == !=
    AND, OR,                   // && ||
    Qmark, Colon, Ende,        // ? : (ternary), Ende (end marker)
    None,
}
```

The token list is `List<object>` — a mix of `Token` enum values (operators) and typed literal/identifier objects (`intLiteral`, `doubleLiteral`, `variable`, `ASTdefine`).

---

## Lexer Construction

```csharp
public Lexer(string strInput, Dictionary<string, define> a_Defines)
```

The Lexer is stateful — it processes the entire string in its constructor. After construction, `Tokens` contains the complete token list.

The lexer walks character by character using `GetNextChar()`:

```csharp
private char GetNextChar()
{
    iPos++;
    if (iPos >= strCode.Length) return (char)0;  // null terminator
    return strCode[iPos];
}
```

---

## Tokenisation Rules

### 1. Whitespace → skip

```csharp
if (char.IsWhiteSpace(ch))
    ch = GetNextChar();  // do nothing, move on
```

### 2. Letters and `_` → identifier or function

The lexer first checks if the current position starts with a known function name followed by `(`:
```csharp
foreach (KeyValuePair<string, Token> kvp in functions)  // "defined", "sqrt", "abs"
{
    if (strCode.Substring(iPos).ToLower().StartsWith(kvp.Key + '('))
    {
        Tokens.Add(kvp.Value);          // e.g. Token.Defined
        if (kvp.Value == Token.Defined) handleDefine = true;
        iPos += kvp.Key.Length - 1;
        break;
    }
}
```

If no function matches, it accumulates characters into an identifier:
```csharp
while (char.IsLetter(ch) || char.IsDigit(ch) || ch == '_')
    accum.Append(ch);  // collect full identifier

string name = accum.ToString();
```

Then decides what to do with the identifier:

**Case 1: `defined()` argument** (`handleDefine = true`):
```csharp
ASTdefine l_def = new ASTdefine();
l_def.Name = name;      // e.g. "FEATURE_NEW_CAN"
Tokens.Add(l_def);
handleDefine = false;
```

This creates a special node that Calculator uses with `a_Defines.ContainsKey()`.

**Case 2: Known define with a non-empty value** → inline substitution:
```csharp
if (a_Defines.ContainsKey(name) && a_Defines[name].value != "")
{
    // INLINE SUBSTITUTION: replace the identifier with its value
    string bauen = strCode.Substring(0, iPosStart);  // before identifier
    bauen += a_Defines[name].value;                   // the value
    bauen += strCode.Substring(iPos);                 // rest of string
    strCode = bauen;    // replace the whole input string!
    iPos = iPosStart - 1;  // rewind position to re-lex the substituted value
    ch = GetNextChar();
}
```

This is **define expansion inline** — the lexer modifies its own input string and restarts lexing from the substituted position. This handles chains like `A → B → 5` recursively.

**Case 3: Unknown identifier or define with empty value** → variable node:
```csharp
variable vari = new variable();
vari.Ident = name;
Tokens.Add(vari);
```

A `variable` node in the Calculator means: look up in `D_Defines` and follow the chain. If still unresolved, treat as a string.

---

### 3. Digits, `.`, `,` → number literal

```csharp
string strCompare = "0123456789.,abcdeflux";
while ((ch != (char)0) && strCompare.Contains(ch.ToString().ToLower()))
```

Supports:
- Decimal integers: `100`, `255`
- Hex integers: `0xFF`, `0x0100`
- Floats: `3.14`, `2,5` (German decimal comma also accepted)
- Unsigned/long suffixes: `100u`, `255ul` (suffix characters stripped)

Hex detection:
```csharp
if (d.StartsWith("0x")) {
    // Convert.ToInt32(hexPart, 16)
}
```

Result: either `intLiteral` or `doubleLiteral` pushed to token list.

---

### 4. Operators → Token enum value

```csharp
private static Dictionary<string, Token> operands = new Dictionary<string, Token> {
    { "<=", Token.LE  },  // Note: MUST come before "<" in iteration!
    { "<",  Token.LT  },
    { ">=", Token.GT  },
    { ">",  Token.GE  },
    { "==", Token.EQ  },
    { "!=", Token.NEQ },
    { "&&", Token.AND },
    { "&",  Token.BitAnd },
    { "||", Token.OR  },
    { "|",  Token.BitOr },
    // ... etc
};

foreach (KeyValuePair<string, Token> kvp in operands)
{
    if (strCode.Substring(iPos).StartsWith(kvp.Key))
    {
        Tokens.Add(kvp.Value);
        iPos += kvp.Key.Length - 1;  // advance past multi-char operator
        break;
    }
}
```

**Critical**: The dictionary iteration order matters for multi-character operators. `<=` must be checked before `<`, otherwise `<=` would be tokenised as `< =` (two tokens). The code comment acknowledges this: *"Kürzere Operatoren verdecken die Längeren!"*

In practice, C# `Dictionary` in .NET 4.x does NOT guarantee insertion order. If `<` comes before `<=` in iteration, `<=` would be misread. This is a latent bug but works in practice because the current .NET version (4.6.1 as per .csproj) preserves insertion order for small dictionaries.

---

## Example Trace

Input: `CS_MODUS==3` (after space removal, CS_MODUS defined as `"1"`)

1. `C` → start identifier, accumulate: `CS_MODUS`
2. Check: `a_Defines["CS_MODUS"].value == "1"` → substitute!
3. `strCode` becomes `"1==3"`, `iPos` rewinds
4. `1` → digit → intLiteral(1)
5. `=` → `==` matches → Token.EQ
6. `3` → digit → intLiteral(3)

Final tokens: `[intLiteral(1), Token.EQ, intLiteral(3)]`

This will evaluate to `1 == 3` → `false`.

---

## AST Node Types (from `AST/AST.cs`)

```csharp
public abstract class Expression { }

public class intLiteral    : Expression { public int Value; }
public class doubleLiteral : Expression { public double Value; }
public class boolLiteral   : Expression { public bool Value; }
public class variable      : Expression { public string Ident; }
public class ASTdefine     : Expression { public string Name; }  // for defined()

public class function  : Expression { public Token Op; public Expression Argument; }
public class unaer     : Expression { public Token Op; public Expression Argument; }
public class binaer    : Expression { public Expression Left; public Token Op; public Expression Right; }
public class ternaer   : Expression { public Expression Condition; public Expression True; public Expression False; }
```

The Lexer produces only the "leaf" types (`intLiteral`, `doubleLiteral`, `variable`, `ASTdefine`) and `Token` enum values. The `function`, `unaer`, `binaer`, `ternaer` tree nodes are created by the Parser.

---

*Next: [[09_ast_optimizer]]*
