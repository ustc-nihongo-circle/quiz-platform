def test_debug_server_returns_imported_question_media(client, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    media_file = tmp_path / "question-banks" / "demo-v1" / "assets" / "diagram.png"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"synthetic-image")

    response = client.get("/media/question-banks/demo-v1/assets/diagram.png")

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"synthetic-image"
