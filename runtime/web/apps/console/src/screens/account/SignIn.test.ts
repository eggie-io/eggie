import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SiteContext } from "../../site/SiteContext";
import { SignIn } from "./SignIn";

describe("SignIn", () => {
  it("offers no Sign in button on a site, where the API refuses it", () => {
    const html = renderToStaticMarkup(
      createElement(
        SiteContext.Provider,
        { value: "foo.eggie.io" },
        createElement(SignIn, { onSignedIn: () => {}, onSessionLost: () => {} }),
      ),
    );
    expect(html).toContain("This server lost its Eggie sign-in");
    expect(html).not.toContain("Sign in</button>");
  });
});
