from edraft.text_utils import extract_authored_email_text


def test_extract_authored_email_text_strips_outlook_header_quote_from_text() -> None:
    content = (
        "Yeah, Jesse, she showed me a plot that indicated more smoke days on Tue and Wed.\n"
        "Jude\n"
        "From:\n"
        "Pierce,Jeffrey <Jeffrey.Pierce@colostate.edu>\n"
        "Sent:\n"
        "Friday, April 3, 2026 14:29\n"
        "To:\n"
        "Burkhardt,Jesse <Jesse.Burkhardt@colostate.edu>\n"
        "Subject:\n"
        "Re: Is there a weekly cycle in Rx fire in the US?\n"
        "\n"
        "This is what Jude said that Brooke found."
    )

    result = extract_authored_email_text(content, "text")

    assert result == "Yeah, Jesse, she showed me a plot that indicated more smoke days on Tue and Wed.\nJude"


def test_extract_authored_email_text_strips_outlook_separator_from_html() -> None:
    content = """
    <html>
      <body>
        <div>Thanks, Jeff. Good info indeed.</div>
        <div>Jude</div>
        <hr>
        <div id="divRplyFwdMsg">
          <div>From: Pierce,Jeffrey &lt;Jeffrey.Pierce@colostate.edu&gt;</div>
          <div>Sent: Friday, April 3, 2026 14:29</div>
        </div>
      </body>
    </html>
    """

    result = extract_authored_email_text(content, "html")

    assert result == "Thanks, Jeff. Good info indeed.\n\nJude"


def test_extract_authored_email_text_strips_mobile_outlook_footer() -> None:
    content = "Glad to hear it was helpful.\n\nGet\nOutlook for Android"

    result = extract_authored_email_text(content, "text")

    assert result == "Glad to hear it was helpful."
