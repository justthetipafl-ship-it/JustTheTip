#!/usr/bin/env Rscript
# JTT NBL data pull — uses nblR (github.com/JaseZiv/nblR) to fetch player box scores + results,
# writes CSVs for build_nbl.py. Mirrors the AFL (fitzRoy) / NRL (nrlR) R-fetch pattern.
suppressMessages(library(nblR))
dir.create("NBL/data", recursive = TRUE, showWarnings = FALSE)

box <- nbl_box_player()
res <- nbl_results(wide_or_long = "wide")

write.csv(box, "NBL/data/box_player.csv", row.names = FALSE, na = "")
write.csv(res, "NBL/data/results.csv",    row.names = FALSE, na = "")
cat("wrote box_player.csv:", nrow(box), "rows; results.csv:", nrow(res), "rows\n")
