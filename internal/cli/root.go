// Package cli defines the shirokuma command-line interface.
package cli

import (
	"fmt"
	"io"

	"github.com/spf13/cobra"
)

const version = "dev"

// NewRootCommand creates the root shirokuma command.
func NewRootCommand(output io.Writer) *cobra.Command {
	root := &cobra.Command{
		Use:           "shirokuma",
		Short:         "Operate the Shirokuma data cloud lab",
		SilenceErrors: true,
		SilenceUsage:  true,
		Version:       version,
	}
	root.SetOut(output)
	root.SetErr(output)

	root.AddCommand(&cobra.Command{
		Use:   "version",
		Short: "Print the shirokuma version",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, err := fmt.Fprintf(cmd.OutOrStdout(), "shirokuma %s\n", version)
			return err
		},
	})

	return root
}

// Execute runs the shirokuma CLI using the process arguments.
func Execute() error {
	return NewRootCommand(nil).Execute()
}
