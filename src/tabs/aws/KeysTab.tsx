import { Box, Text } from "@primer/react";
import { PageHeader } from '@primer/react/drafts';

const KeysTab = (): JSX.Element => {
  return (
    <>
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title>Keys</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>
      <Box>
        <Text>Keys.</Text>
      </Box>
    </>
  );
}

export default KeysTab;
