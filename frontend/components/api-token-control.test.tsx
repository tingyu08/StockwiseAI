/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { expect, it } from "vitest";

import { ApiTokenControl } from "./api-token-control";

it("lets the user store a private API token for this browser session", () => {
  render(createElement(ApiTokenControl));
  fireEvent.click(screen.getByRole("button", { name: /API Token/i }));
  fireEvent.change(screen.getByLabelText("API Token"), {
    target: { value: "single-user-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "儲存" }));

  expect(sessionStorage.getItem("stockwise-api-token")).toBe("single-user-secret");
});
