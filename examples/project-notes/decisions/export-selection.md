# Export selected orders

During the sample run, the project owner chose the selected rows as the export boundary. This lets a support operator check which orders will leave the application before downloading the CSV.

The implementation task must carry that boundary through filters and pagination. The reviewer should verify that unselected rows stay out and that customer names containing quotation marks survive a CSV round trip.

Source: [Fictional acceptance example](../evidence/selection.md). Revisit when users need scheduled exports or an explicit export-all action.
