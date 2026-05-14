"""Integration tests for the Books CRUD endpoints (Task 7.1)."""

from __future__ import annotations

from sqlmodel import Session

from book_alerter.db import models


def test_post_books_happy_path_normalizes_isbn(api_client):
    resp = api_client.post(
        "/api/books",
        json={
            "isbn": "0241638194",  # ISBN-10 — should normalize to 13
            "title": "T",
            "author": "A",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["isbn13"] == "9780241638194"
    assert body["title"] == "T"
    assert body["author"] == "A"
    assert isinstance(body["id"], int) and body["id"] > 0
    assert body["status"] == "active"
    # stats is present (zero-observation case)
    assert body["stats"]["observation_count"] == 0
    assert body["stats"]["current_best_total_minor"] is None


def test_post_books_duplicate_isbn_returns_409(api_client):
    payload = {"isbn": "9780241638194", "title": "T", "author": "A"}
    r1 = api_client.post("/api/books", json=payload)
    assert r1.status_code == 201
    r2 = api_client.post("/api/books", json=payload)
    assert r2.status_code == 409


def test_post_books_invalid_isbn_returns_422(api_client):
    resp = api_client.post(
        "/api/books",
        json={"isbn": "not-an-isbn", "title": "T", "author": "A"},
    )
    assert resp.status_code == 422


def test_get_books_list_returns_books_with_stats(api_client):
    api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    )
    resp = api_client.get("/api/books")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["isbn13"] == "9780241638194"
    assert "stats" in body[0]
    assert body[0]["stats"]["observation_count"] == 0


def test_get_book_by_id_happy_path(api_client):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    resp = api_client.get(f"/api/books/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert "stats" in body


def test_get_book_unknown_id_returns_404(api_client):
    resp = api_client.get("/api/books/99999")
    assert resp.status_code == 404


def test_patch_book_updates_fields(api_client, engine_with_view):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    bid = created["id"]
    resp = api_client.patch(
        f"/api/books/{bid}",
        json={"target_price_minor": 500, "status": "bought"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_price_minor"] == 500
    assert body["status"] == "bought"

    # Verify the DB row reflects it.
    with Session(engine_with_view) as s:
        db_book = s.get(models.Book, bid)
        assert db_book is not None
        assert db_book.target_price_minor == 500
        assert db_book.status == "bought"


def test_patch_book_unknown_id_returns_404(api_client):
    resp = api_client.patch("/api/books/99999", json={"target_price_minor": 1})
    assert resp.status_code == 404


def test_patch_book_empty_body_is_noop(api_client):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    resp = api_client.patch(f"/api/books/{created['id']}", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == created["title"]
    assert body["author"] == created["author"]
    assert body["status"] == created["status"]


def test_delete_book_soft_delete_default(api_client, engine_with_view):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    bid = created["id"]
    resp = api_client.delete(f"/api/books/{bid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    # Row still exists.
    with Session(engine_with_view) as s:
        db_book = s.get(models.Book, bid)
        assert db_book is not None
        assert db_book.status == "archived"


def test_delete_book_hard_removes_row(api_client, engine_with_view):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    bid = created["id"]
    resp = api_client.delete(f"/api/books/{bid}?hard=true")
    assert resp.status_code == 200

    # Row gone — GET returns 404.
    resp2 = api_client.get(f"/api/books/{bid}")
    assert resp2.status_code == 404

    with Session(engine_with_view) as s:
        assert s.get(models.Book, bid) is None


def test_get_books_excludes_archived_by_default(api_client):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    api_client.delete(f"/api/books/{created['id']}")  # soft-delete

    resp = api_client.get("/api/books")
    assert resp.status_code == 200
    assert resp.json() == []

    resp_all = api_client.get("/api/books?include_archived=true")
    assert resp_all.status_code == 200
    assert len(resp_all.json()) == 1
