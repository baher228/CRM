import { expect, test } from "@playwright/test";

test("dialogs manage focus, validation and Escape", async ({ page }) => {
  await page.goto("/");
  const trigger = page.getByRole("button", { name: /New Ctrl N|New/ }).first();
  await trigger.focus();
  await trigger.press("Enter");

  const dialog = page.getByRole("dialog", { name: "Quick create" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-describedby", /.+/);

  const accountName = dialog.getByLabel("Account name");
  await expect(accountName).toBeFocused();
  await expect(accountName).toHaveAttribute("required", "");

  const submit = dialog.getByRole("button", { name: "Create account" });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(dialog).toBeVisible();
  await expect(accountName).toBeFocused();

  await submit.focus();
  await page.keyboard.press("Tab");
  await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("zero-outline fields retain a visible composite focus indicator", async ({ page }) => {
  await page.goto("/accounts");
  const listSearch = page.getByRole("searchbox", { name: "Search Accounts" });
  await listSearch.focus();
  const listFocus = await listSearch.evaluate((input) => {
    const style = getComputedStyle(input.closest(".search-field"));
    return { style: style.outlineStyle, width: style.outlineWidth };
  });
  expect(listFocus.style).not.toBe("none");
  expect(listFocus.width).not.toBe("0px");

  await page.keyboard.press(process.platform === "darwin" ? "Meta+K" : "Control+K");
  const command = page.getByRole("dialog", { name: "Command workspace" });
  const commandSearch = command.getByRole("searchbox", { name: "Search CRM records and pages" });
  await expect(commandSearch).toBeFocused();
  const commandFocus = await commandSearch.evaluate((input) => {
    const style = getComputedStyle(input.closest(".command-input"));
    return { style: style.outlineStyle, width: style.outlineWidth };
  });
  expect(commandFocus.style).not.toBe("none");
  expect(commandFocus.width).not.toBe("0px");
});

test("reduced motion removes non-essential transitions and animations", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const motion = await page.getByRole("button", { name: /New Ctrl N|New/ }).first().evaluate((button) => {
    const style = getComputedStyle(button);
    return { animation: style.animationName, transition: style.transitionDuration };
  });
  expect(motion.animation).toBe("none");
  expect(motion.transition).toBe("0s");
});
