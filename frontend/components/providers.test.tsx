/** @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiRequest } from "@/lib/api";
import { Providers } from "./providers";

vi.mock("@/lib/api", () => ({ apiRequest: vi.fn() }));
const requestMock = vi.mocked(apiRequest);

beforeEach(() => requestMock.mockReset());
afterEach(cleanup);

it("allows the first visitor to create the owner account", async () => {
  requestMock.mockImplementation(async (path) => {
    if (path === undefined) return undefined as never;
    if (path === "/auth/session") return { authenticated: false, registration_open: true, username: null };
    if (path === "/auth/register") return { authenticated: true, registration_open: false, username: "owner" };
    throw new Error(`unexpected path: ${path}`);
  });
  render(createElement(Providers, null, createElement("div", null, "private app")));
  fireEvent.change(await screen.findByLabelText("帳號"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("密碼"), { target: { value: "x" } });
  fireEvent.change(screen.getByLabelText("確認密碼"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "建立帳號" }));
  await screen.findByText("private app");
  expect(requestMock).toHaveBeenCalledWith("/auth/register", { method: "POST", body: { username: "owner", password: "x" } });
});

it("logs into an existing owner account and logs out", async () => {
  requestMock.mockImplementation(async (path) => {
    if (path === undefined) return undefined as never;
    if (path === "/auth/session") return { authenticated: false, registration_open: false, username: null };
    if (path === "/auth/login") return { authenticated: true, registration_open: false, username: "owner" };
    if (path === "/auth/logout") return { authenticated: false };
    throw new Error(`unexpected path: ${path}`);
  });
  render(createElement(Providers, null, createElement("div", null, "private app")));
  fireEvent.change(await screen.findByLabelText("帳號"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("密碼"), { target: { value: "secret" } });
  fireEvent.click(screen.getByRole("button", { name: "登入" }));
  await screen.findByText("private app");
  fireEvent.click(screen.getByRole("button", { name: "owner · 登出" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "登入" })).toBeInTheDocument());
});
