from operator import length_hint
from functools import partial
from itertools import permutations
from collections import Counter
from collections.abc import Sequence

from cons import cons
from cons.core import ConsNull, ConsPair
from unification import isvar, reify, var
from unification.core import isground

from .core import (
    eq,
    EarlyGoalError,
    conde,
    lall,
    lanyseq,
    goaleval,
)


def heado(head, coll):
    """Construct a goal stating that head is the head of coll.

    See Also
    --------
        tailo
        conso
    """
    return eq(cons(head, var()), coll)


def tailo(tail, coll):
    """Construct a goal stating that tail is the tail of coll.

    See Also
    --------
        heado
        conso
    """
    return eq(cons(var(), tail), coll)


def conso(h, t, l):
    """Construct a goal stating that cons h + t == l."""
    return eq(cons(h, t), l)


def nullo(*args, refs=None, default_ConsNull=list):
    """Create a goal asserting that one or more terms are a/the same `ConsNull` type.

    `ConsNull` types return proper Python collections when used as a CDR value
    in a CONS (e.g. `cons(1, []) == [1]`).

    This goal doesn't require that all args be unifiable; only that they have
    the same `ConsNull` type.  Unlike the classic `lall(eq(x, []), eq(y, x))`
    `conde`-branch idiom used when recursively walking a single sequence via
    `conso`, this allows us to perform the same essential function while
    walking distinct lists that do not necessarily terminate on the same
    iteration.

    Parameters
    ----------
    args: tuple of objects
        The terms to consider as an instance of the `ConsNull` type
    refs: tuple of objects
        The terms to use as reference types.  These are not unified with the
        `ConsNull` type, instead they are used to constrain the `ConsNull`
        types considered valid.
    default_ConsNull: type
        The sequence type to use when all logic variables are unground.

    """

    def nullo_goal(s):

        nonlocal args, default_ConsNull

        if refs is not None:
            refs_rf = reify(refs, s)
        else:
            refs_rf = ()

        args_rf = reify(args, s)

        arg_null_types = set(
            # Get an empty instance of the type
            type(a)
            for a in args_rf + refs_rf
            # `ConsPair` and `ConsNull` types that are not literally `ConsPair`s
            if isinstance(a, (ConsPair, ConsNull)) and not issubclass(type(a), ConsPair)
        )

        try:
            null_type = arg_null_types.pop()
        except KeyError:
            null_type = default_ConsNull

        if len(arg_null_types) > 0 and any(a != null_type for a in arg_null_types):
            # Mismatching null types: fail.
            return

        g = lall(*[eq(a, null_type()) for a in args_rf])

        yield from goaleval(g)(s)

    return nullo_goal


def itero(l, nullo_refs=None, default_ConsNull=list):
    """Construct a goal asserting that a term is an iterable type.

    This is a generic version of the standard `listo` that accounts for
    different iterable types supported by `cons` in Python.

    See `nullo`
    """

    def itero_goal(S):
        nonlocal l, nullo_refs, default_ConsNull
        l_rf = reify(l, S)
        c, d = var(), var()
        g = conde(
            [nullo(l_rf, refs=nullo_refs, default_ConsNull=default_ConsNull)],
            [conso(c, d, l_rf), itero(d, default_ConsNull=default_ConsNull)],
        )
        yield from goaleval(g)(S)

    return itero_goal


def membero(x, ls):
    """Construct a goal stating that x is an item of coll."""

    def membero_goal(S):
        nonlocal x, ls

        x_rf, ls_rf = reify((x, ls), S)
        a, d = var(), var()

        g = lall(conso(a, d, ls), conde([eq(a, x)], [membero(x, d)]))

        yield from goaleval(g)(S)

    return membero_goal


def appendo(l, s, out, default_ConsNull=list):
    """Construct a goal for the relation l + s = ls.

    See Byrd thesis pg. 247
    https://scholarworks.iu.edu/dspace/bitstream/handle/2022/8777/Byrd_indiana_0093A_10344.pdf
    """

    def appendo_goal(S):
        nonlocal l, s, out

        l_rf, s_rf, out_rf = reify((l, s, out), S)

        a, d, res = var(prefix="a"), var(prefix="d"), var(prefix="res")

        _nullo = partial(nullo, default_ConsNull=default_ConsNull)

        g = conde(
            [
                # All empty
                _nullo(s_rf, l_rf, out_rf),
            ],
            [
                # `l` is empty
                conso(a, d, out_rf),
                eq(s_rf, out_rf),
                _nullo(l_rf, refs=(s_rf, out_rf)),
            ],
            [
                conso(a, d, l_rf),
                conso(a, res, out_rf),
                appendo(d, s_rf, res, default_ConsNull=default_ConsNull),
            ],
        )

        yield from goaleval(g)(S)

    return appendo_goal


def rembero(x, l, o, default_ConsNull=list):
    """Remove the first occurrence of `x` in `l` resulting in `o`."""

    from .constraints import neq

    def rembero_goal(s):
        nonlocal x, l, o

        x_rf, l_rf, o_rf = reify((x, l, o), s)

        l_car, l_cdr, r = var(), var(), var()

        g = conde(
            [nullo(l_rf, o_rf, default_ConsNull=default_ConsNull),],
            [conso(l_car, l_cdr, l_rf), eq(x_rf, l_car), eq(l_cdr, o_rf),],
            [
                conso(l_car, l_cdr, l_rf),
                neq(l_car, x),
                conso(l_car, r, o_rf),
                rembero(x_rf, l_cdr, r, default_ConsNull=default_ConsNull),
            ],
        )

        yield from goaleval(g)(s)

    return rembero_goal


def permuteo(a, b, inner_eq=eq, default_ConsNull=list):
    """Construct a goal asserting equality or sequences under permutation.

    For example, (1, 2, 2) equates to (2, 1, 2) under permutation
    >>> from kanren import var, run, permuteo
    >>> x = var()
    >>> run(0, x, permuteo(x, (1, 2)))
    ((1, 2), (2, 1))

    >>> run(0, x, permuteo((2, 1, x), (2, 1, 2)))
    (2,)
    """

    def permuteo_goal(S):
        nonlocal a, b, default_ConsNull, inner_eq

        a_rf, b_rf = reify((a, b), S)

        # If the lengths differ, then fail
        a_len, b_len = length_hint(a_rf, -1), length_hint(b_rf, -1)
        if a_len > 0 and b_len > 0 and a_len != b_len:
            return

        if isinstance(a_rf, Sequence):

            a_type = type(a_rf)

            if isinstance(b_rf, Sequence):

                # `a` and `b` are sequences, so let's see if we can
                # pull out all the equal elements using their hashes.

                b_type = type(b_rf)

                if a_type != b_type:
                    return

                try:
                    cntr_a, cntr_b = Counter(a_rf), Counter(b_rf)
                    rdcd_a, rdcd_b = cntr_a - cntr_b, cntr_b - cntr_a
                    a_rf, b_rf = tuple(rdcd_a.elements()), b_type(rdcd_b.elements())
                except TypeError:
                    # TODO: We could probably get more coverage for this case
                    # by using `HashableForm`.
                    pass

                # If they're both ground, then simply check that one is a
                # permutation of the other and be done
                if isground(a_rf, S) and isground(b_rf, S):
                    if a_rf in permutations(b_rf):
                        yield S
                        return
                    else:
                        return

            # Unify all permutations of the sequence `a` with `b`
            yield from lanyseq(inner_eq(b_rf, a_type(i)) for i in permutations(a_rf))(S)

        elif isinstance(b_rf, Sequence):

            b_type = type(b_rf)

            # Unify all permutations of the sequence `b` with `a`
            yield from lanyseq(inner_eq(a_rf, b_type(i)) for i in permutations(b_rf))(S)

        else:

            # None of the arguments are proper sequences, so state that one
            # should be and apply `permuteo` to that.

            a_itero_g = itero(
                a_rf, nullo_refs=(b_rf,), default_ConsNull=default_ConsNull
            )

            for S_new in a_itero_g(S):
                a_new = reify(a_rf, S_new)
                a_type = type(a_new)
                yield from lanyseq(
                    inner_eq(b_rf, a_type(i)) for i in permutations(a_new)
                )(S_new)

    return permuteo_goal


# For backward compatibility
permuteq = permuteo


def goalify(func, name=None):  # pragma: noqa
    """Convert a Python function into kanren goal.

    >>> from kanren import run, goalify, var, membero
    >>> typo = goalify(type)
    >>> x = var('x')
    >>> run(0, x, membero(x, (1, 'cat', 'hat', 2)), (typo, x, str))
    ('cat', 'hat')

    Goals go both ways.  Here are all of the types in the collection

    >>> typ = var('typ')
    >>> results = run(0, typ, membero(x, (1, 'cat', 'hat', 2)), (typo, x, typ))
    >>> print([result.__name__ for result in results])
    ['int', 'str']
    """

    def funco(inputs, out):  # pragma: noqa
        if isvar(inputs):
            raise EarlyGoalError()
        else:
            if isinstance(inputs, (tuple, list)):
                if any(map(isvar, inputs)):
                    raise EarlyGoalError()
                return (eq, func(*inputs), out)
            else:
                return (eq, func(inputs), out)

    name = name or (func.__name__ + "o")
    funco.__name__ = name

    return funco
