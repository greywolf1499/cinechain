"""JIT image cache: SSRF guard, disk caching, HTTP revalidation, stale fallback."""

import httpx
import pytest
import respx

from app.services import image_cache

URL = "https://a.ltrbxd.com/resized/avatar.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 64


@pytest.mark.parametrize("url", [
    "http://a.ltrbxd.com/x.jpg",
    "https://evil.example.com/x.jpg",
    "https://ltrbxd.com.evil.example.com/x.jpg",
    "https://user:pw@a.ltrbxd.com/x.jpg",
    "https://a.ltrbxd.com:8443/x.jpg",
    "file:///etc/passwd",
    "https://127.0.0.1/x.jpg",
])
def test_validate_rejects_non_letterboxd_urls(url):
    with pytest.raises(image_cache.ImageProxyError):
        image_cache.validate_image_url(url)


def test_validate_accepts_letterboxd_cdn_hosts():
    assert image_cache.validate_image_url(URL) == URL
    assert image_cache.validate_image_url("https://letterboxd.com/a.jpg")


async def test_first_request_fetches_then_serves_from_disk(config_dir):
    with respx.mock:
        route = respx.get(URL).mock(return_value=httpx.Response(
            200, content=JPEG, headers={"content-type": "image/jpeg", "cache-control": "max-age=86400"}))
        async with httpx.AsyncClient() as client:
            first = await image_cache.get_image(client, URL)
            second = await image_cache.get_image(client, URL)

    assert route.call_count == 1
    assert first.path == second.path
    assert first.path.read_bytes() == JPEG
    assert first.path.parent == config_dir / "cache_images"
    assert second.content_type == "image/jpeg"


async def test_stale_entry_revalidates_with_etag_and_keeps_body_on_304(config_dir):
    with respx.mock:
        route = respx.get(URL).mock(side_effect=[
            httpx.Response(200, content=JPEG, headers={
                "content-type": "image/jpeg", "etag": '"abc"', "cache-control": "no-cache"}),
            httpx.Response(304, headers={"cache-control": "max-age=3600"}),
        ])
        async with httpx.AsyncClient() as client:
            await image_cache.get_image(client, URL)
            _, meta_path = image_cache._paths(URL)
            meta = image_cache._read_meta(meta_path)
            meta["expires_at"] = 0  # force staleness
            meta_path.write_text(__import__("json").dumps(meta))
            again = await image_cache.get_image(client, URL)

    assert route.call_count == 2
    assert route.calls[1].request.headers["if-none-match"] == '"abc"'
    assert again.path.read_bytes() == JPEG


async def test_serves_stale_copy_when_upstream_fails(config_dir):
    with respx.mock:
        respx.get(URL).mock(side_effect=[
            httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"}),
            httpx.Response(500),
        ])
        async with httpx.AsyncClient() as client:
            await image_cache.get_image(client, URL)
            _, meta_path = image_cache._paths(URL)
            meta = image_cache._read_meta(meta_path)
            meta["expires_at"] = 0
            meta_path.write_text(__import__("json").dumps(meta))
            stale = await image_cache.get_image(client, URL)

    assert stale.path.read_bytes() == JPEG


async def test_rejects_non_image_oversized_and_offsite_redirects(config_dir):
    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(
                200, content=b"<html>", headers={"content-type": "text/html"}))
            with pytest.raises(image_cache.ImageProxyError):
                await image_cache.get_image(client, URL)

        with respx.mock:
            big = "https://a.ltrbxd.com/big.jpg"
            respx.get(big).mock(return_value=httpx.Response(
                200, content=b"x" * (image_cache.MAX_IMAGE_BYTES + 1), headers={"content-type": "image/jpeg"}))
            with pytest.raises(image_cache.ImageProxyError):
                await image_cache.get_image(client, big)

        with respx.mock:
            hop = "https://a.ltrbxd.com/hop.jpg"
            respx.get(hop).mock(return_value=httpx.Response(
                302, headers={"location": "https://evil.example.com/steal.jpg"}))
            with pytest.raises(image_cache.ImageProxyError):
                await image_cache.get_image(client, hop)


async def test_prune_evicts_oldest_until_under_cap(config_dir):
    directory = image_cache.cache_dir()
    for index in range(3):
        (directory / f"k{index}.img").write_bytes(b"x" * 100)
        (directory / f"k{index}.json").write_text("{}")
        import os
        os.utime(directory / f"k{index}.img", (index, index))

    removed = image_cache.prune_cache(max_bytes=150)

    assert removed == 2
    assert [p.name for p in directory.glob("*.img")] == ["k2.img"]
    assert not (directory / "k0.json").exists()
