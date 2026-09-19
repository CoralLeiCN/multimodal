# Data processing specification

## Special date rule: circa year

When a date has no structured `from` or `to` bounds, interpret `c.` followed
by a year as that single year for filtering. For example, `c.1993` produces
`date_from=1993` and `date_to=1993`. This is a project convention for search;
the source date remains approximate.

Preserve the original date text, including `c.`, for display and provenance.
Use the same numeric interval in SQLite and Qdrant. A filter containing 1993
matches `c.1993`; a filter covering only 1992 or 1994 does not.

Accept either case for `c.`, surrounding whitespace, and whitespace between
the prefix and year. Apply the existing year limits: -9999 through 9999,
excluding zero. Negative years represent BCE.

| Source value without structured bounds | Filter start | Filter end |
| --- | --- | --- |
| `c.1993` | 1993 | 1993 |
| `C. 1993` | 1993 | 1993 |
| `c.-400` | -400 | -400 |
| `c.0` | No interval | No interval |

Structured bounds take precedence. For example, a source value of `c.1993`
with `from=1990` and `to=1995` retains the interval 1990–1995. Partial or
invalid structured bounds contribute no interval, following the existing
date validation rules.

Other approximate expressions, such as `circa 1993`, `c.1990-1995`, or
`probably 1993`, require their own explicit processing rules. They currently
contribute no interval unless valid structured bounds are supplied.

The shared metadata extractor applies this rule during bronze ingestion and
metadata refresh. A future Parquet indexing reader must apply the same rule.
The current silver-to-gold converter preserves date strings as supplied.
Existing indexed metadata picks up the rule through metadata refresh, which
preserves image embeddings. See [date sources](data_spec.md#search-filter-metadata)
and [metadata refresh](cronjob/README.md#refresh-search-filter-metadata).
