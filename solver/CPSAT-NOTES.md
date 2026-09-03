# CP-SAT cheat sheet

API mechanics only — a reference to keep open while writing `model.py`. The
modelling decisions are yours.

```python
from ortools.sat.python import cp_model
model = cp_model.CpModel()
```

## Variables

```python
x = model.NewIntVar(lb, ub, "name")      # integer, domain [lb, ub] inclusive
b = model.NewBoolVar("name")             # domain {0, 1}, also usable as a literal
model.NewIntVarFromDomain(
    cp_model.Domain.FromIntervals([[0, 100], [200, 300]]), "name")  # gaps allowed
```

**Integers only.** No floats, no datetimes. Everything in `Problem` is
already seconds since local midnight.

**The domain is a constraint, not a type annotation.** `NewIntVar(28800, 39600,
"start_J1")` tells the solver before it starts searching that J1 cannot begin
before 08:00 or after 11:00. Every value you exclude up front is a value the
search never explores. `ProblemJob.latest_start_s` exists for exactly this.

## Constraints

```python
model.Add(x + y <= 10)                   # Python operators are overloaded
model.Add(x == y)
model.Add(sum(bools) == 1)

model.AddExactlyOne(literals)            # exactly one true
model.AddAtMostOne(literals)             # at most one true
model.AddBoolOr(literals)                # at least one true
model.AddImplication(a, b)               # a => b
```

### `.OnlyEnforceIf(literal)` — the important one

Makes a constraint conditional. The workhorse of any routing model:

```python
model.Add(start_b >= end_a + travel_ab).OnlyEnforceIf(arc_a_to_b)
```

Reads as: *if* b follows a, *then* b cannot start until a has finished and the
van has driven there. When `arc_a_to_b` is false the constraint is inert.

Takes a list for a conjunction, and `.Not()` for negation:

```python
model.Add(...).OnlyEnforceIf([lit_a, lit_b.Not()])
```

Note `.OnlyEnforceIf` attaches to the constraint object returned by `Add`, not
to the model. Only *some* constraint types support it — linear constraints do.

## Interval variables

```python
iv = model.NewIntervalVar(start, size, end, "name")
# enforces start + size == end automatically
```

Optional intervals exist only when a literal is true — which is what you need
when a job might not be assigned to a given technician:

```python
iv = model.NewOptionalIntervalVar(start, size, end, is_present, "name")
```

```python
model.AddNoOverlap([iv1, iv2, ...])      # no two overlap in time
```

**`AddNoOverlap` does not know about travel.** It stops one technician doing
two jobs at once, but it will happily put the end of one job at 10:00 and the
start of the next at 10:00 on the other side of the city. Travel has to come
from somewhere else — either sequencing literals with `OnlyEnforceIf`, or
`AddCircuit`.

## `AddCircuit` — the routing primitive

```python
model.AddCircuit(arcs)   # arcs: list of (tail, head, literal)
```

Node indices must be **contiguous from 0**. Build a local index for each
technician's own node set; do not pass `problem` node numbers directly unless
they happen to start at 0 with no gaps.

It enforces: the literals that are true form **one single circuit** visiting
every node that is not self-looped.

### Self-loops mean "skip this node"

An arc `(i, i, lit)` is a self-loop. If `lit` is true, node `i` is **not
visited**. This is how optional visits work, and it is the part people miss:

```python
visited_i = self_loop_i.Not()
```

Every node needs either a real incoming arc or a self-loop, or the model is
infeasible. Including the depot — an unused technician needs a way to say so.

### It is a *closed* circuit

`AddCircuit` produces a tour that returns to where it started. Our routes are
open: start at home, end at the last job, no return leg (that was the phase 1
decision). The usual way to reconcile this is to let the arc back to the depot
exist structurally but contribute **zero** to the time and cost dimensions —
the circuit closes, the technician's day does not.

### Cost

`AddCircuit` enforces *structure*, not cost. Travel time is yours to attach,
via `OnlyEnforceIf` on the arc literals.

## Solving

```python
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 30.0
solver.parameters.num_search_workers = 1     # reproducible
status = solver.Solve(model)

solver.Value(x)          # after a solution is found
solver.BooleanValue(b)
solver.StatusName(status)
solver.WallTime()
solver.ObjectiveValue()  # phase 6
```

| status | meaning |
|---|---|
| `OPTIMAL` | proved best. With no objective, means "found one, nothing to optimise" |
| `FEASIBLE` | found a solution, hit the time limit before proving optimality |
| `INFEASIBLE` | **proved** no solution exists. Your model or your data is over-constrained |
| `MODEL_INVALID` | malformed model — call `model.Validate()` for the reason |
| `UNKNOWN` | time limit hit with nothing found. Not the same as INFEASIBLE |

`INFEASIBLE` vs `UNKNOWN` is the distinction that matters when debugging: the
first is a proof, the second is a shrug.

## Debugging

```python
print(model.Validate())          # "" when the model is well-formed
print(len(model.Proto().variables), "variables")
print(len(model.Proto().constraints), "constraints")
solver.parameters.log_search_progress = True
```

`AddAssumptions` + `SufficientAssumptionsForInfeasibility()` narrows down
*which* constraints conflict when you get INFEASIBLE and expected otherwise.

## Hints (`AddHint`)

A hint is a suggested value, not a constraint — CP-SAT may ignore it or
improve on it. It cannot make a model infeasible.

```python
model.AddHint(var, value)          # per variable, before Solve()
```

**Hint the variables the constraint is actually made of.** This is the trap
that cost the most here. `AddCircuit`'s real variables are the *arc literals*;
`visit[job]` and `start[job]` are consequences of them. Hinting only the
assignments says "here are the right technicians, now work out the routes
yourself" — and the routes are most of the problem.

Measured on 80 jobs / 15 technicians:

| hint | 5s | 30s |
|---|---|---|
| `visit` + `start` only | UNKNOWN, 0 assigned | 0–26 assigned |
| plus every arc literal | falls back to greedy, 61–70 | 67–74, FEASIBLE |

Hint arcs **completely** — the ones on the route to 1 *and* every other arc in
the block to 0. A half-set circuit is not a solution CP-SAT can repair cheaply.
If any needed arc was pruned from the model, skip that block entirely rather
than hinting it incoherently.

**A hint does not guarantee a solution.** If presolve does not finish inside
the limit, `Solve` still returns UNKNOWN with nothing. When you have a feasible
schedule in hand, return that rather than an empty one — and label it, so
nothing downstream mistakes greedy's answer for the solver's.

## Traps

- **Silent infeasibility from a bad domain.** `NewIntVar(lb, ub)` with `lb > ub`
  makes the whole model infeasible with no error at the point of the mistake.
  If a job's window is shorter than its duration, `latest_start_s` goes below
  `hard_start_s` and you get INFEASIBLE with nothing pointing at the cause.
- **`AddNoOverlap` without travel** — see above. Looks right, permits teleporting.
- **Forgetting depot self-loops** for unused technicians.
- **Non-contiguous node indices** in `AddCircuit`.
- **`b.Not()` is a literal, not a constraint.** `model.Add(b.Not())` is not how
  you force b false; use `model.Add(b == 0)`.
- **Parallel workers are nondeterministic.** Set `num_search_workers = 1` while
  learning.
- **A partial hint on a circuit is close to no hint at all.** See Hints above.

## No objective in phase 4

With nothing to minimise, CP-SAT returns the **first feasible solution** it
finds. Expect it to look silly — everything on one technician, absurd ordering,
huge waits. That is correct behaviour for a feasibility-only model, and it is
what phase 6 fixes. Do not tune the model to make it look nicer; check that
it is *valid*, which is what phase 5 is for.
