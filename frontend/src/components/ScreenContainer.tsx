import { PropsWithChildren } from "react";
import { StyleProp, StyleSheet, ViewStyle } from "react-native";
import { SafeAreaView, Edge } from "react-native-safe-area-context";
import { theme } from "@/src/theme";

/**
 * Shared root container for every screen and full-screen modal.
 *
 * Guarantees that no visible content ever sits under the OS status bar,
 * notch, or home indicator — by wrapping the screen in a SafeAreaView
 * from `react-native-safe-area-context`. Defaults to reserving the top
 * inset only (matching the app's existing tab screens); pass `edges` to
 * customise.
 *
 * WHY use this instead of the react-native `SafeAreaView`?
 *   - `react-native/SafeAreaView` respects iOS insets only and does the
 *     wrong thing on Android + web.
 *   - `react-native-safe-area-context/SafeAreaView` uses live inset
 *     measurements from a single `SafeAreaProvider` at the app root, so
 *     it works consistently across iOS, Android, and Expo web.
 *
 * FOR HEADER-SPECIFIC insets, use `ScreenHeader` — that component
 * reserves `insets.top` INSIDE a coloured header so the status bar text
 * stays legible on the brand green.
 */
type Props = PropsWithChildren<{
  /** Which safe-area edges to reserve. Defaults to ['top','left','right'] —
   *  we intentionally omit 'bottom' so ScrollView content can push a
   *  paddingBottom={insets.bottom} inside instead. */
  edges?: Edge[];
  /** Root background colour. Defaults to the app background. */
  background?: string;
  /** Extra styles to merge on top of the flex:1 root style. */
  style?: StyleProp<ViewStyle>;
}>;

export default function ScreenContainer({
  children,
  edges = ["top", "left", "right"],
  background,
  style,
}: Props) {
  return (
    <SafeAreaView
      edges={edges}
      style={[
        styles.root,
        { backgroundColor: background ?? theme.colors.background },
        style,
      ]}
    >
      {children}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
