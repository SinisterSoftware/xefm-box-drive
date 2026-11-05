from box_drive import logic

def url_from_cursor(ctx):
    """Copy and open the Box URL for the item under the cursor."""
    logic.box_url_from_cursor(ctx)

def url_from_panel(ctx):
    """Copy and open the Box URL for the current folder."""
    logic.box_url_from_panel(ctx)

def open_clipboard(ctx):
    """Go to the local Box Drive path represented by the clipboard."""
    logic.box_open_clipboard(ctx)
