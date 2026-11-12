# Dialogue pools

Every `*.lines` file here is plain text:

    # comment
    [category.key]
    one line per entry, with {slots} such as {name}, {food}, {network}

Keys are looked up most-specific first: `feed.like.happy` falls back to
`feed.like`, then `feed`. Lines are picked with a no-repeat ring buffer, so a
line is not reused until most of its pool has been used. Add lines freely; a
bigger pool simply repeats less. Avoid stray `{` `}` that are not slots.
