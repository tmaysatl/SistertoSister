import { PropsWithChildren, ReactNode } from "react";
import { StyleSheet, View, ViewStyle, StyleProp } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { theme } from "@/src/theme";

/**
 * Coloured screen header that correctly reserves the OS top safe-area inset.
 *
 * Layout:
 *   ┌───────────────────────────────────────────────┐
 *   │   (insets.top — reserved for status bar)      │  ← paddingTop
 *   │ [ left ]     [ title / subtitle ]  [ right ]  │  ← HEADER_CONTENT_HEIGHT
 *   └───────────────────────────────────────────────┘
 *
 * The status bar (time / battery / return chip on iOS) is drawn on top of
 * the header background but its text sits over the reserved inset area,
 * never on top of the title or the action buttons.
 *
 * Total height = HEADER_CONTENT_HEIGHT + insets.top.
 *
 * Use this in every full-screen modal that has a coloured top bar
 * (DynamicFormRenderer, SignatureCaptureModal, PdfViewerModal, etc.). Tab
 * screens don't need it — they wrap themselves in ScreenContainer /
 * SafeAreaView edges={['top']} so their neutral background covers the
 * inset naturally.
 */
export const HEADER_CONTENT_HEIGHT = 52;

type Props = PropsWithChildren<{
  /** Left slot (typically a close/back button). */
  left?: ReactNode;
  /** Right slot (typically primary action like Submit). */
  right?: ReactNode;
  /** Header background colour. Defaults to brand primary (dark green). */
  background?: string;
  /** Extra style to merge onto the header wrapper. */
  style?: StyleProp<ViewStyle>;
}>;

export default function ScreenHeader({
  left,
  right,
  children,
  background,
  style,
}: Props) {
  const insets = useSafeAreaInsets();
  return (
    <View
      style={[
        styles.wrapper,
        {
          backgroundColor: background ?? theme.colors.brandPrimary,
          paddingTop: insets.top,
          height: HEADER_CONTENT_HEIGHT + insets.top,
        },
        style,
      ]}
    >
      <View style={styles.row}>
        {left ? <View style={styles.side}>{left}</View> : <View style={styles.sidePlaceholder} />}
        <View style={styles.center}>{children}</View>
        {right ? <View style={styles.side}>{right}</View> : <View style={styles.sidePlaceholder} />}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    width: "100%",
  },
  row: {
    height: HEADER_CONTENT_HEIGHT,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  center: {
    flex: 1,
    justifyContent: "center",
  },
  side: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  sidePlaceholder: {
    minWidth: 0,
  },
});
