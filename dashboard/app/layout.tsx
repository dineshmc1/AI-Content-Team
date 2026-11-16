import type {Metadata} from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Build AI With Dinesh — Local Agency",
  description: "Local content-agency control room",
};

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}
