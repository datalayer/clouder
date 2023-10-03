import { Box, Text } from "@primer/react";
import { PageHeader } from '@primer/react/drafts';

const SSHKeysTab = (): JSX.Element => {
  return (
    <>
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title>SSH Keys</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>
      <Box>
        <Text>SSH eys.</Text>
      </Box>
    </>
  );
}

export default SSHKeysTab;
