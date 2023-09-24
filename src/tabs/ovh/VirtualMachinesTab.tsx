import { Box, Text } from '@primer/react';
import { PageHeader } from '@primer/react/drafts';

const VirtualMachinesTab = (): JSX.Element => {
  return (
    <>
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title>Virtual Machines</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>
      <Box>
        <Text>Virtual machines.</Text>
      </Box>
    </>
  );
}

export default VirtualMachinesTab;
