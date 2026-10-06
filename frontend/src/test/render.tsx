import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { AppDataProvider } from "../state/appData";
import { ToastProvider } from "../state/toast";
import { Toaster } from "../components/Toaster";

export function renderWithProviders(ui: ReactElement) {
  return render(
    <ToastProvider>
      <AppDataProvider>
        {ui}
        <Toaster />
      </AppDataProvider>
    </ToastProvider>,
  );
}
