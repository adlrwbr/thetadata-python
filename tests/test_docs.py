"""Contains tests for the tutorial section of the documentation."""
import pytest
import docs
from docs.get_last import get_last
from docs.list_roots import get_roots
from docs.end_of_day import end_of_day
from docs import manipulate_df, manipulate_series


def test_docs_end_of_day():
    """Test the end of day blog snippet."""
    end_of_day()
    manipulate_df.main()


def test_docs_list_roots():
    """Test the list roots tutorial."""
    get_roots()
    manipulate_series.main()


@pytest.mark.skip(
    reason="Live data currently only works during trading hours."
)  # TODO: remove
def test_docs_get_last():
    """Test the live data tutorial."""
    get_last()
