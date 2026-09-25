import pytest


@pytest.mark.smoke
def test_qapbot_error_str_includes_context_when_present():
    from clashcontrol.exceptions import ClashControlError

    exc = ClashControlError("boom", context={"user_id": "123"})
    assert exc.message == "boom"
    assert exc.context["user_id"] == "123"
    assert "boom" in str(exc)
    assert "user_id=123" in str(exc)


@pytest.mark.smoke
def test_specific_exception_is_qapbot_error_subclass():
    from clashcontrol.exceptions import ConfigurationError, ClashControlError

    exc = ConfigurationError("bad config")
    assert isinstance(exc, ClashControlError)
    assert exc.message == "bad config"
