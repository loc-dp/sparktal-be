from apps.core.pagination import DefaultPagination


def test_pagination_defaults() -> None:
    paginator = DefaultPagination()

    assert paginator.page_size == 25
    assert paginator.page_size_query_param == "page_size"
    assert paginator.max_page_size == 100
