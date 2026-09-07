function lcafConfirmDelete(form, kind) {
  var expected = form.getAttribute("data-confirm-name") || "";
  var typed = window.prompt(
    "PERMANENT DELETE\n\n" +
      "This removes the " +
      (kind || "item") +
      " and related history. It cannot be undone.\n\n" +
      'Type the exact name to confirm:\n"' +
      expected +
      '"',
    ""
  );
  if (typed === null) return false;
  if (typed !== expected) {
    window.alert("Name did not match. Delete cancelled.");
    return false;
  }
  var field = form.querySelector('input[name="confirm_name"]');
  if (field) field.value = typed;
  return true;
}
