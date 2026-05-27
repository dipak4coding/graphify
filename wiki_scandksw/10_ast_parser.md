# AST Parser — Building the Expression Tree

Tags: #ast #parser #recursivedescent
Links: [[00_INDEX]] | [[09_ast_optimizer]] | [[11_ast_compiler]]

---

## Purpose

The Parser takes the flat token list from the Optimizer and builds a **tree structure** representing the expression's meaning. This tree correctly encodes operator precedence and associativity.

```
Token list  →  [Parser]  →  Expression tree
```

---

## Operator Precedence Table

The Parser uses a ranking dictionary where **lower number = higher precedence** (binds tighter):

```csharp
private static Dictionary<Token, int> ranking = new Dictionary<Token, int> {
    { Token.BraceLeft,  2 },   // ()
    { Token.BraceRight, 2 },
    { Token.Neg,        3 },   // unary -
    { Token.NOT,        3 },   // unary !
    { Token.Sqrt,       4 },   // sqrt()
    { Token.Defined,    4 },   // defined()
    { Token.Abs,        4 },   // abs()
    { Token.Mul,        5 },   // *
    { Token.Div,        5 },   // /
    { Token.Modulo,     5 },   // %
    { Token.Add,        6 },   // +
    { Token.Sub,        6 },   // -
    { Token.LT,         8 },   // <
    { Token.LE,         8 },   // <=
    { Token.GT,         8 },   // >
    { Token.GE,         8 },   // >=
    { Token.EQ,         9 },   // ==
    { Token.NEQ,        9 },   // !=
    { Token.BitAnd,    10 },   // &
    { Token.Xor,       11 },   // ^
    { Token.BitOr,     12 },   // |
    { Token.AND,       13 },   // &&
    { Token.OR,        14 },   // ||
    { Token.Qmark,     15 },   // ? (ternary)
    { Token.Colon,     15 },   // :
    { Token.None,      99 },
};
```

This matches the standard C operator precedence table.

---

## The Recursive Descent Algorithm

```csharp
public Parser(List<object> tok)
{
    tokens = tok;
    indexP = 0;
    result = ParseExpression(false, 99, out b_brace);
    // rank 99 = accept anything (lowest priority)
}
```

**`ParseExpression(bool a_straightBack, int a_rank, out bool o_brace)`**:

- `a_rank` = the precedence level of the calling context ("don't consume tokens with rank > this")
- `a_straightBack` = true means "return after the next atom, don't look for operators"
- `o_brace` = signals "a closing `)` or `:` was seen — return to caller"

The do-while loop:
```csharp
do {
    if (token is literal or variable)
        retur = token;   // leaf node
    
    else if (token is Token)
        switch (token) {
            case BraceLeft: 
                retur = ParseExpression(false, 99, out o_brace);  // new sub-expression
                break;
            
            case BraceRight:
            case Colon:
                o_brace = true;  // signal caller to stop
                break;
            
            case binary operators (Mul, Add, EQ, AND, etc.):
                if (a_rank < t_rank)   // if new op has LOWER priority (higher rank number)
                    { indexP--; a_straightBack = true; }  // back up, let caller handle it
                else
                    { be = new binaer(); be.Left = retur; be.Right = ParseExpression(...); retur = be; }
                break;
            
            case NOT, Neg:   // unary
                un = new unaer(); un.Argument = ParseExpression(true, t_rank, ...);
                retur = un;
                break;
            
            case Defined, Abs, Sqrt:   // functions
                fkn = new function(); fkn.Argument = ParseExpression(true, t_rank, ...);
                retur = fkn;
                break;
            
            case Qmark:   // ternary
                trnr = new ternaer();
                trnr.Condition = retur;
                trnr.True  = ParseExpression(false, t_rank, ...);  // reads until ":"
                trnr.False = ParseExpression(false, t_rank, ...);  // reads until ")" or end
                retur = trnr;
                break;
        }
} while (indexP < tokens.Count && !a_straightBack && !o_brace);
```

---

## Precedence in Action

Consider: `1 + 2 == 3 + 4 && 5 <= 6`

Reading left to right:
1. Parse `1` → intLiteral(1)
2. See `+` (rank 6) ≤ current rank 99 → create binaer, recurse right with rank=6
3. Right side starts parsing `2` → intLiteral(2)
4. See `==` (rank 9) > current rank 6 → back up! Return intLiteral(2) to left side
5. Left side: `binaer(1, Add, 2)` = `(1+2)`
6. Back at top level, see `==` (rank 9) ≤ 99 → create binaer, recurse right with rank=9
7. Right parses `3+4` → `binaer(3, Add, 4)`
8. See `&&` (rank 13) > 9 → back up. Return `(3+4)`
9. Top level: `binaer((1+2), EQ, (3+4))`
10. See `&&` (rank 13) ≤ 99 → create binaer, recurse right
11. Right parses `5<=6` → `binaer(5, LE, 6)`
12. Final tree: `binaer(binaer((1+2),EQ,(3+4)), AND, binaer(5,LE,6))`

Which correctly represents: `((1+2)==(3+4)) && (5<=6)`

---

## Bracket Handling

`(` triggers a new sub-expression call with fresh rank 99:
```csharp
case Token.BraceLeft:
    retur = ParseExpression(false, 99, out o_brace);
    o_brace = false;  // clear the flag set by ")"
    break;
```

`)` sets `o_brace=true` which causes the do-while to exit, returning control to the parent expression.

---

## The `print()` Debug Method

```csharp
private void print(Expression expr, int a_l)
```

Recursively prints the expression tree with indentation. For `(1+2)*3`:
```
    1
Add
    2
Mul
3
```

Used by the `output()` method during development/debugging.

---

## Ternary Operator

The ternary `?:` is handled specially because it has three parts:

```csharp
case Token.Qmark:
    ternaer trnr = new ternaer();
    trnr.Condition = retur;        // what was already parsed (before ?)
    trnr.True  = ParseExpression(false, t_rank, out o_brace);  // reads until ":"
    o_brace = false;
    trnr.False = ParseExpression(false, t_rank, out o_brace);  // reads until ")" or end
    retur = trnr;
    break;
```

For `TRUE ? 1+2 : 3+4`:
- Condition = `variable("TRUE")`
- True = `binaer(1, Add, 2)`  — parsing stops when `:` sets `o_brace=true`
- False = `binaer(3, Add, 4)` — parsing stops at end

Nested ternary `TRUE?12:FALSE?34:56`:
- Outer condition = TRUE
- Outer True = 12
- Outer False: parser encounters another `?` → creates nested ternaer
  - Inner condition = FALSE
  - Inner True = 34
  - Inner False = 56

---

*Next: [[11_ast_compiler]]*
