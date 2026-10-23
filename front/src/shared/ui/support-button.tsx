"use client";

import { ButtonGroup, Link } from "@heroui/react";
import { HeadsetHelp } from "iconoir-react";

/**
 * The editable part of a message to the developer: the person writes what went
 * wrong; everything above it is fixed letterhead.
 */
export const SUPPORT_BODY_INTRO =
    "Здесь вы можете описать обнаруженные проблемы или пожелания по улучшению работы проекта. Если это возможно, прикрепите иллюстрационный материал (скриншоты, схемы) — это поможет лучше вас понять. Обращение будет отправлено направлено напрямую разработчику сайта, согласование с техподдержкой не требуется. Если проблема срочная, вы также можете позвонить по телефону.";

/**
 * Build the `mailto:` a person opens to write to the developer.
 *
 * The subject names the project («Обращение по РепортКоллектор Ремонт», «…
 * КППК», «… Сырьё»), so one mailbox can triage by project. The body is the
 * letterhead followed by blank lines the person types into. `encodeURIComponent`
 * leaves `!'()*` alone: legal in a mailto query, but some mail clients mangle
 * them, so they are escaped too.
 */
export function supportMailto(to: string, projectName: string): string {
    const encode = (value: string) =>
        encodeURIComponent(value).replace(
            /[!'()*]/g,
            (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
        );
    const subject = `Обращение по РепортКоллектор ${projectName}`;
    const body = `${SUPPORT_BODY_INTRO}\n\n\n`;
    // `@` stays literal in the address — it is legal there and every client
    // handles it, while a percent-encoded `@` confuses some of them.
    return `mailto:${to}?subject=${encode(subject)}&body=${encode(body)}`;
}

/**
 * «Связаться с разработчиком» — the one way to reach the site's developer.
 *
 * Two shapes, one link builder:
 *   - `appearance="collapsed"` (the header): icon-only until the pointer is
 *     over it, then the label expands. Focus expands it as well — a keyboard
 *     user tabbing onto an icon-only control would otherwise face an unlabeled
 *     one. `pinned` keeps it open (the «no access» screen does that).
 *   - `appearance="button"` (inside a dialog/error card): a real label-button
 *     next to «Обновить», optionally tinted as the primary way out.
 *
 * `mailto:` needs an anchor, not a Button — the button component treats an
 * activation as handled and would swallow the link. The button shape therefore
 * reuses HeroUI's own `Link` styling with the button classes and, inside a
 * `ButtonGroup`, the `.button` class that carries the group corner radii.
 */
export function SupportButton({
    projectName,
    supportMail,
    pinned = false,
    appearance = "collapsed",
    label = "Связаться с разработчиком",
    primary = false,
    withSeparator = false,
}: {
    /** The project the message is about — it goes into the subject. */
    projectName: string;
    /** `SUPPORT_MAIL` from the environment. Empty = the control is not rendered. */
    supportMail: string;
    /** Keep the collapsed label open (the «no access» screen). */
    pinned?: boolean;
    appearance?: "collapsed" | "button";
    label?: string;
    /** Tint the button shape as the primary way out (the crash screen). */
    primary?: boolean;
    /** Render the group's dividing rule before the icon (the second button of a pair). */
    withSeparator?: boolean;
}) {
    if (!supportMail) return null;
    const href = supportMailto(supportMail, projectName);
    const ariaLabel = `${label} по проекту «${projectName}»`;
    if (appearance === "button") {
        return (
            <Link
                href={href}
                className={[
                    // `no-underline`: HeroUI's Link underlines on hover by
                    // default — a button-shaped control must not.
                    "button button--tertiary inline-flex items-center gap-2 no-underline",
                    primary && "support-link--primary",
                ]
                    .filter(Boolean)
                    .join(" ")}
                aria-label={ariaLabel}
            >
                {withSeparator ? <ButtonGroup.Separator /> : null}
                <HeadsetHelp aria-hidden="true" />
                {label}
            </Link>
        );
    }
    return (
        <Link
            href={href}
            className={["button button--tertiary support-link", pinned && "support-link--pinned"]
                .filter(Boolean)
                .join(" ")}
            aria-label={ariaLabel}
        >
            <HeadsetHelp aria-hidden="true" />
            <span className="support-link__label">{label}</span>
        </Link>
    );
}
