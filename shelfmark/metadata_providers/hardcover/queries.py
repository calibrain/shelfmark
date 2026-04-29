"""GraphQL operations used by the Hardcover metadata provider."""

LIST_LOOKUP_QUERY = """
query LookupListsBySlug($slug: String!) {
    lists(where: {slug: {_eq: $slug}}, limit: 20) {
        id
        slug
        user {
            username
        }
    }
}
"""

LIST_BOOKS_BY_ID_QUERY = """
query GetListBooksById($id: Int!, $limit: Int!, $offset: Int!) {
    lists(where: {id: {_eq: $id}}, limit: 1) {
        name
        slug
        user {
            username
        }
        books_count
        list_books(order_by: {position: asc}, limit: $limit, offset: $offset) {
            book {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
            }
        }
    }
}
"""

USER_LISTS_QUERY = """
query GetUserLists {
    me {
        id
        username
        want_to_read_count: user_books_aggregate(where: {status_id: {_eq: 1}}) {
            aggregate {
                count(columns: [book_id], distinct: true)
            }
        }
        currently_reading_count: user_books_aggregate(where: {status_id: {_eq: 2}}) {
            aggregate {
                count(columns: [book_id], distinct: true)
            }
        }
        read_count: user_books_aggregate(where: {status_id: {_eq: 3}}) {
            aggregate {
                count(columns: [book_id], distinct: true)
            }
        }
        did_not_finish_count: user_books_aggregate(where: {status_id: {_eq: 5}}) {
            aggregate {
                count(columns: [book_id], distinct: true)
            }
        }
        lists(order_by: {name: asc}) {
            id
            name
            slug
            books_count
        }
        followed_lists(order_by: {created_at: desc}) {
            list {
                id
                name
                slug
                books_count
                user {
                    username
                }
            }
        }
    }
}
"""

USER_BOOKS_BY_STATUS_QUERY = """
query GetCurrentUserBooksByStatus($statusId: Int!, $limit: Int!, $offset: Int!) {
    me {
        status_books: user_books(
            where: {status_id: {_eq: $statusId}}
            distinct_on: [book_id]
            order_by: [{book_id: asc}, {created_at: desc}]
            limit: $limit
            offset: $offset
        ) {
            book {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
            }
        }
        status_books_aggregate: user_books_aggregate(where: {status_id: {_eq: $statusId}}) {
            aggregate {
                count(columns: [book_id], distinct: true)
            }
        }
    }
}
"""

BOOK_TARGET_MEMBERSHIP_QUERY = """
query GetBookTargetMembership($bookId: Int!) {
    me {
        user_books(where: {book_id: {_eq: $bookId}}, limit: 1, order_by: [{created_at: desc}]) {
            id
            status_id
        }
        lists {
            id
            list_books(where: {book_id: {_eq: $bookId}}, limit: 1) {
                id
            }
        }
    }
}
"""

BOOK_TARGET_MEMBERSHIP_BATCH_QUERY = """
query GetBookTargetMembershipBatch($bookIds: [Int!]!) {
    me {
        user_books(where: {book_id: {_in: $bookIds}}, order_by: [{created_at: desc}]) {
            id
            book_id
            status_id
        }
        lists {
            id
            list_books(where: {book_id: {_in: $bookIds}}) {
                id
                book_id
            }
        }
    }
}
"""

INSERT_USER_BOOK_MUTATION = """
mutation AddBookToStatus($bookId: Int!, $statusId: Int!) {
    insert_user_book(object: {book_id: $bookId, status_id: $statusId}) {
        id
        error
        user_book {
            id
            book_id
            status_id
        }
    }
}
"""

UPDATE_USER_BOOK_MUTATION = """
mutation UpdateBookStatus($userBookId: Int!, $statusId: Int!) {
    update_user_book(id: $userBookId, object: {status_id: $statusId}) {
        id
        error
        user_book {
            id
            book_id
            status_id
        }
    }
}
"""

DELETE_USER_BOOK_MUTATION = """
mutation RemoveBookStatus($userBookId: Int!) {
    delete_user_book(id: $userBookId) {
        id
        book_id
        user_id
    }
}
"""

INSERT_LIST_BOOK_MUTATION = """
mutation AddBookToList($bookId: Int!, $listId: Int!) {
    insert_list_book(object: {book_id: $bookId, list_id: $listId}) {
        id
        list_book {
            id
            book_id
            list_id
        }
    }
}
"""

DELETE_LIST_BOOK_MUTATION = """
mutation RemoveBookFromList($listBookId: Int!) {
    delete_list_book(id: $listBookId) {
        id
        list_id
    }
}
"""

SEARCH_FIELD_OPTIONS_QUERY = """
query SearchFieldOptions(
    $query: String!,
    $queryType: String!,
    $limit: Int!,
    $page: Int!,
    $sort: String,
    $fields: String,
    $weights: String
) {
    search(
        query: $query,
        query_type: $queryType,
        per_page: $limit,
        page: $page,
        sort: $sort,
        fields: $fields,
        weights: $weights
    ) {
        results
    }
}
"""

SERIES_BY_AUTHOR_IDS_QUERY = """
query SeriesByAuthorIds($authorIds: [Int!], $limit: Int!) {
    series(
        where: {
            author_id: {_in: $authorIds},
            canonical_id: {_is_null: true},
            state: {_eq: "active"}
        },
        limit: $limit,
        order_by: [{primary_books_count: desc_nulls_last}, {books_count: desc}, {name: asc}]
    ) {
        id
        name
        primary_books_count
        books_count
        author {
            name
        }
    }
}
"""

SERIES_BOOKS_BY_ID_QUERY = """
query GetSeriesBooks($seriesId: Int!) {
    series(where: {id: {_eq: $seriesId}}, limit: 1) {
        id
        name
        primary_books_count
        book_series(
            where: {
                book: {
                    canonical_id: {_is_null: true},
                    state: {_in: ["normalized", "normalizing"]}
                }
            }
            order_by: [{position: asc_nulls_last}, {book_id: asc}]
        ) {
            position
            book {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                compilation
                editions_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
            }
        }
    }
}
"""

AUTHOR_BOOKS_BY_ID_QUERY = """
query GetAuthorBooks($authorId: Int!, $limit: Int!, $offset: Int!) {
    authors(where: {id: {_eq: $authorId}}, limit: 1) {
        name
        contributions(
            where: {
                contributable_type: {_eq: "Book"},
                book: {
                    canonical_id: {_is_null: true},
                    state: {_in: ["normalized", "normalizing"]}
                }
            },
            order_by: [
                {book: {users_count: desc_nulls_last}},
                {book: {ratings_count: desc_nulls_last}},
                {book: {release_date: asc_nulls_last}},
                {book: {id: asc}}
            ],
            limit: $limit,
            offset: $offset
        ) {
            contribution
            book {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                compilation
                editions_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
            }
        }
        contributions_aggregate(
            where: {
                contributable_type: {_eq: "Book"},
                book: {
                    canonical_id: {_is_null: true},
                    state: {_in: ["normalized", "normalizing"]}
                }
            }
        ) {
            aggregate {
                count
            }
        }
    }
}
"""

SEARCH_BOOKS_WITH_FIELDS_QUERY = """
query SearchBooks(
    $query: String!,
    $limit: Int!,
    $page: Int!,
    $sort: String,
    $fields: String,
    $weights: String
) {
    search(
        query: $query,
        query_type: "Book",
        per_page: $limit,
        page: $page,
        sort: $sort,
        fields: $fields,
        weights: $weights
    ) {
        results
    }
}
"""

SEARCH_BOOKS_QUERY = """
query SearchBooks($query: String!, $limit: Int!, $page: Int!, $sort: String) {
    search(query: $query, query_type: "Book", per_page: $limit, page: $page, sort: $sort) {
        results
    }
}
"""

GET_BOOK_QUERY = """
query GetBook($id: Int!) {
    books(where: {id: {_eq: $id}}, limit: 1) {
        id
        title
        subtitle
        slug
        release_date
        headline
        description
        pages
        cached_image
        cached_tags
        cached_contributors
        contributions(where: {contribution: {_eq: "Author"}}) {
            author {
                name
            }
        }
        default_physical_edition {
            isbn_10
            isbn_13
        }
        featured_book_series {
            position
            series {
                id
                name
                primary_books_count
            }
        }
        editions(
            distinct_on: language_id
            order_by: [{language_id: asc}, {users_count: desc}]
            limit: 200
        ) {
            title
            language {
                language
                code2
                code3
            }
        }
    }
}
"""

SEARCH_BY_ISBN_QUERY = """
query SearchByISBN($isbn: String!) {
    editions(
        where: {
            _or: [
                {isbn_10: {_eq: $isbn}},
                {isbn_13: {_eq: $isbn}}
            ]
        },
        limit: 1
    ) {
        isbn_10
        isbn_13
        book {
            id
            title
            subtitle
            slug
            release_date
            headline
            description
            pages
            cached_image
            cached_tags
            contributions(where: {contribution: {_eq: "Author"}}) {
                author {
                    name
                }
            }
        }
    }
}
"""
