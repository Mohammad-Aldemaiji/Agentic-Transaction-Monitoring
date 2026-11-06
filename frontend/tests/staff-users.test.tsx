import { expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import StaffUsers from "../src/StaffUsers";
vi.mock("../src/api", () => ({
  api: vi.fn().mockResolvedValue({
    items: [
      {
        id: "self",
        name: "Current admin",
        email: "admin@example.com",
        role: "admin",
        active: true,
        version: 1,
        is_self: true,
        face_enrolled: true,
      },
      {
        id: "other",
        name: "Other user",
        email: "analyst@example.com",
        role: "analyst",
        active: true,
        version: 1,
        is_self: false,
        face_enrolled: true,
      },
    ],
  }),
}));
it("disables all self-management controls while allowing management of another user", async () => {
  render(<StaffUsers enabled />);
  const self = (await screen.findByText("admin@example.com")).closest("tr")!;
  expect(within(self).getByRole("combobox")).toBeDisabled();
  for (const name of [
    "Disable",
    "Send password reset link",
    "Reset face sign-in",
    "Delete user",
  ])
    expect(within(self).getByRole("button", { name })).toBeDisabled();
  const other = screen.getByText("analyst@example.com").closest("tr")!;
  expect(within(other).getByRole("combobox")).toBeEnabled();
  for (const name of [
    "Disable",
    "Send password reset link",
    "Reset face sign-in",
    "Delete user",
  ])
    expect(within(other).getByRole("button", { name })).toBeEnabled();
  expect(within(other).getByText("Set up")).toBeInTheDocument();
});
