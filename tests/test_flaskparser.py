# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import threading

from werkzeug.exceptions import HTTPException
import mock
import pytest

from flask import Flask
from webargs import fields, ValidationError, missing
from webargs.flaskparser import parser, abort
from webargs.core import MARSHMALLOW_VERSION_INFO, json

from .apps.flask_app import app
from webargs.testing import CommonTestCase


class TestFlaskParser(CommonTestCase):
    def create_app(self):
        return app

    def test_parsing_view_args(self, testapp):
        res = testapp.get("/echo_view_arg/42")
        assert res.json == {"view_arg": 42}

    def test_parsing_invalid_view_arg(self, testapp):
        res = testapp.get("/echo_view_arg/foo", expect_errors=True)
        assert res.status_code == 422
        assert res.json == {"view_arg": ["Not a valid integer."]}

    def test_use_args_with_view_args_parsing(self, testapp):
        res = testapp.get("/echo_view_arg_use_args/42")
        assert res.json == {"view_arg": 42}

    def test_use_args_on_a_method_view(self, testapp):
        res = testapp.post("/echo_method_view_use_args", {"val": 42})
        assert res.json == {"val": 42}

    def test_use_kwargs_on_a_method_view(self, testapp):
        res = testapp.post("/echo_method_view_use_kwargs", {"val": 42})
        assert res.json == {"val": 42}

    def test_use_kwargs_with_missing_data(self, testapp):
        res = testapp.post("/echo_use_kwargs_missing", {"username": "foo"})
        assert res.json == {"username": "foo"}

    # regression test for https://github.com/marshmallow-code/webargs/issues/145
    def test_nested_many_with_data_key(self, testapp):
        res = testapp.post_json("/echo_nested_many_data_key", {"x_field": [{"id": 42}]})
        # https://github.com/marshmallow-code/marshmallow/pull/714
        if MARSHMALLOW_VERSION_INFO[0] < 3:
            assert res.json == {"x_field": [{"id": 42}]}

        res = testapp.post_json("/echo_nested_many_data_key", {"X-Field": [{"id": 24}]})
        assert res.json == {"x_field": [{"id": 24}]}

        res = testapp.post_json("/echo_nested_many_data_key", {})
        assert res.json == {}


@mock.patch("webargs.flaskparser.abort")
def test_abort_called_on_validation_error(mock_abort):
    app = Flask("testapp")

    def validate(x):
        return x == 42

    argmap = {"value": fields.Field(validate=validate)}
    with app.test_request_context(
        "/foo",
        method="post",
        data=json.dumps({"value": 41}),
        content_type="application/json",
    ):
        parser.parse(argmap)
    mock_abort.assert_called()
    abort_args, abort_kwargs = mock_abort.call_args
    assert abort_args[0] == 422
    expected_msg = "Invalid value."
    assert abort_kwargs["messages"]["value"] == [expected_msg]
    assert type(abort_kwargs["exc"]) == ValidationError


def test_parse_form_returns_missing_if_no_form():
    req = mock.Mock()
    req.form.get.side_effect = AttributeError("no form")
    assert parser.parse_form(req, "foo", fields.Field()) is missing


def test_abort_with_message():
    with pytest.raises(HTTPException) as excinfo:
        abort(400, message="custom error message")
    assert excinfo.value.data["message"] == "custom error message"


def test_abort_has_serializable_data():
    with pytest.raises(HTTPException) as excinfo:
        abort(400, message="custom error message")
    serialized_error = json.dumps(excinfo.value.data)
    error = json.loads(serialized_error)
    assert isinstance(error, dict)
    assert error["message"] == "custom error message"

    with pytest.raises(HTTPException) as excinfo:
        abort(
            400,
            message="custom error message",
            exc=ValidationError("custom error message"),
        )
    serialized_error = json.dumps(excinfo.value.data)
    error = json.loads(serialized_error)
    assert isinstance(error, dict)
    assert error["message"] == "custom error message"


def test_json_cache_race_condition():
    app = Flask("testapp")
    lock = threading.Lock()
    lock.acquire()

    class MyField(fields.Field):
        def _deserialize(self, value, attr, data, **kwargs):
            with lock:
                return value

    argmap = {"value": MyField()}
    results = {}

    def thread_fn(value):
        with app.test_request_context(
            "/foo",
            method="post",
            data=json.dumps({"value": value}),
            content_type="application/json",
        ):
            results[value] = parser.parse(argmap)["value"]

    t1 = threading.Thread(target=thread_fn, args=(42,))
    t2 = threading.Thread(target=thread_fn, args=(23,))
    t1.start()
    t2.start()
    lock.release()
    t1.join()
    t2.join()
    # ensure we didn't get contaminated by a parallel request
    assert results[42] == 42
    assert results[23] == 23
